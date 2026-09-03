package com.noteflow.conversations;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ConversationService {
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final LocalWorkspaceService users;
    private final TaskDispatchService tasks;

    public ConversationService(JdbcTemplate jdbc, ObjectMapper json, LocalWorkspaceService users, TaskDispatchService tasks) {
        this.jdbc = jdbc;
        this.json = json;
        this.users = users;
        this.tasks = tasks;
    }

    @Transactional
    public Map<String, Object> create(String title) {
        UUID id = UUID.randomUUID();
        UUID userId = users.currentUserId();
        String safeTitle = title == null || title.isBlank() ? "New conversation" : title.trim();
        jdbc.update("INSERT INTO rag_conversations(id,user_id,title) VALUES (?,?,?)", id, userId, safeTitle);
        return Map.of("id", id, "title", safeTitle, "status", "ACTIVE");
    }

    public List<Map<String, Object>> list() {
        return jdbc.queryForList("""
            SELECT id,title,status,last_message_at,created_at,updated_at
              FROM rag_conversations WHERE user_id=? ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 100
            """, users.currentUserId());
    }

    public List<Map<String, Object>> messages(UUID conversationId) {
        return messages(conversationId, 100, null);
    }

    /**
     * Lists messages with citations. When {@code before} is provided, only
     * the page immediately preceding that message (within the conversation)
     * is returned. Pages are fetched newest-first for efficient keyset
     * pagination, then reordered oldest-first for chat rendering. {@code limit}
     * is clamped to [1, 300].
     */
    public List<Map<String, Object>> messages(UUID conversationId, int limit, UUID before) {
        requireOwnedConversation(conversationId);
        int safeLimit = Math.max(1, Math.min(300, limit));
        List<Object> args = new ArrayList<>();
        args.add(conversationId);
        String beforeClause = "";
        if (before != null) {
            beforeClause = " AND (created_at,id) < (SELECT created_at,id FROM rag_messages WHERE id=? AND conversation_id=?) ";
            args.add(before);
            args.add(conversationId);
        }
        args.add(safeLimit);
        List<Map<String, Object>> rows = jdbc.queryForList("""
            SELECT id,role,status,content_markdown,model_provider,model_name,structured_response_json,error_message,created_at,completed_at
              FROM (
                    SELECT id,role,status,content_markdown,model_provider,model_name,
                           structured_response_json,error_message,created_at,completed_at
                      FROM rag_messages
                     WHERE conversation_id=?""" + beforeClause + """
                     ORDER BY created_at DESC,id DESC
                     LIMIT ?
                   ) page
             ORDER BY created_at,id
            """, args.toArray());
        Map<Object, List<Map<String, Object>>> citations = loadCitations(
            rows.stream().map(row -> row.get("id")).toList());
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            result.add(withCitations(row, citations.get(row.get("id"))));
        }
        return result;
    }

    public Map<String, Object> message(UUID messageId) {
        Map<String, Object> row = jdbc.queryForMap("""
            SELECT m.id,m.conversation_id,m.role,m.status,m.content_markdown,m.model_provider,m.model_name,m.structured_response_json,
                   m.error_message,m.created_at,m.completed_at
              FROM rag_messages m JOIN rag_conversations c ON c.id=m.conversation_id
             WHERE m.id=? AND c.user_id=?
            """, messageId, users.currentUserId());
        return withCitations(row, null);
    }

    public Map<String, Object> messageTrace(UUID conversationId, UUID messageId) {
        Map<String, Object> message = message(messageId);
        if (!conversationId.equals(message.get("conversation_id"))) {
            throw new IllegalArgumentException("Message not found in conversation");
        }
        Object structured = message.get("structuredResponse");
        if (structured instanceof Map<?, ?> structuredMap) {
            Object agent = structuredMap.get("agent");
            if (agent instanceof Map<?, ?> agentMap) {
                Object trace = agentMap.containsKey("trace") ? agentMap.get("trace") : List.of();
                return Map.of(
                    "messageId", messageId,
                    "agent", agentMap,
                    "trace", trace
                );
            }
        }
        return Map.of("messageId", messageId, "agent", Map.of("enabled", false), "trace", List.of());
    }

    @Transactional
    public Map<String, Object> send(
            UUID conversationId,
            String content,
            List<UUID> pdfIds,
            List<UUID> aiNoteIds,
            boolean allowAgentWrites,
            boolean allowAgentDeletes) {
        UUID userId = users.currentUserId();
        requireOwnedConversation(conversationId);
        String text = content == null ? "" : content.trim();
        if (text.isEmpty() || text.length() > 20_000) {
            throw new IllegalArgumentException("Message must contain between 1 and 20,000 characters");
        }
        List<UUID> pdfScope = normalized(pdfIds);
        List<UUID> noteScope = normalized(aiNoteIds);
        requireOwnedDocuments(userId, pdfScope, noteScope);
        jdbc.update("""
            UPDATE rag_conversations
               SET selected_pdf_document_ids=?::jsonb,selected_ai_note_document_ids=?::jsonb,
                   title=CASE WHEN title='New conversation' THEN ? ELSE title END,updated_at=NOW()
             WHERE id=? AND user_id=?
            """, toJson(pdfScope), toJson(noteScope), abbreviatedTitle(text), conversationId, userId);

        UUID userMessageId = UUID.randomUUID();
        UUID assistantMessageId = UUID.randomUUID();
        List<String> capabilities = requestedCapabilities(text, allowAgentWrites, allowAgentDeletes);
        jdbc.update("""
            INSERT INTO rag_messages(id,conversation_id,role,status,content_markdown,token_count,completed_at)
            VALUES (?,?, 'USER','COMPLETED',?,?,NOW())
            """, userMessageId, conversationId, text, estimateTokens(text));
        jdbc.update("""
            INSERT INTO rag_messages(id,conversation_id,role,status,content_markdown,metadata_json)
            VALUES (?,?, 'ASSISTANT','GENERATING','',?)
            """, assistantMessageId, conversationId, toJson(Map.of(
                "userMessageId", userMessageId.toString(),
                "agentCapabilities", capabilities
            )));
        jdbc.update("UPDATE rag_conversations SET last_message_at=NOW(),updated_at=NOW() WHERE id=?", conversationId);

        Task task = tasks.createConversationAndEnqueue(userId, conversationId, assistantMessageId);
        jdbc.update("INSERT INTO conversation_task_targets(task_id,conversation_id,message_id) VALUES (?,?,?)",
            task.getId(), conversationId, assistantMessageId);
        return Map.of(
            "conversationId", conversationId,
            "userMessageId", userMessageId,
            "assistantMessageId", assistantMessageId,
            "taskId", task.getId(),
            "status", "GENERATING"
        );
    }

    private Map<String, Object> withCitations(Map<String, Object> row, List<Map<String, Object>> citations) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(row);
        Object rawStructured = result.remove("structured_response_json");
        result.put("structuredResponse", parseStructuredResponse(rawStructured));
        if (citations != null) {
            result.put("citations", citations);
            return result;
        }
        result.put("citations", loadCitations(List.of(row.get("id"))).getOrDefault(row.get("id"), List.of()));
        return result;
    }

    /** One IN query for all requested messages, grouped in memory (avoids N+1). */
    private Map<Object, List<Map<String, Object>>> loadCitations(List<Object> messageIds) {
        if (messageIds.isEmpty()) return Map.of();
        String placeholders = String.join(",", java.util.Collections.nCopies(messageIds.size(), "?"));
        Map<Object, List<Map<String, Object>>> grouped = new LinkedHashMap<>();
        for (Map<String, Object> citation : jdbc.queryForList("""
            SELECT message_id,citation_index,document_id,source_title AS document_title,page_start,page_end,
                   evidence_snapshot AS quote_text,retrieval_score AS similarity_score
              FROM rag_message_citations WHERE message_id IN (%s)
             ORDER BY message_id,citation_index
            """.formatted(placeholders), messageIds.toArray())) {
            grouped.computeIfAbsent(citation.remove("message_id"), ignored -> new ArrayList<>()).add(citation);
        }
        return grouped;
    }

    private List<String> requestedCapabilities(String message, boolean allowWrites, boolean allowDeletes) {
        if (!allowWrites) return List.of();
        String value = message.toLowerCase(java.util.Locale.ROOT);
        List<String> result = new ArrayList<>();
        if (containsAny(value, "edit", "rewrite", "rename", "insert", "append", "save", "create note",
                "summary", "summarize", "study guide", "example", "study plan",
                "修改", "编辑", "改写", "重写", "重命名", "插入", "追加", "保存", "创建笔记",
                "总结", "摘要", "学习指南", "例子", "学习计划")) {
            result.add("workspace:write");
        }
        if (containsAny(value, "quiz", "flashcard", "practice question", "generate notes", "study guide",
                "测验", "题目", "闪卡", "练习题", "生成笔记", "学习指南")) {
            result.add("study:write");
        }
        if (containsAny(value, "learning goal", "preference", "mastery", "study plan", "learning feedback",
                "学习目标", "偏好", "掌握度", "学习计划", "学习反馈")) {
            result.add("learning:write");
        }
        if (allowDeletes && containsAny(value, "delete", "remove", "erase", "删除", "移除", "清除")) {
            if (!result.contains("workspace:write")) result.add("workspace:write");
            result.add("workspace:delete");
        }
        return List.copyOf(result);
    }

    private boolean containsAny(String value, String... markers) {
        return java.util.Arrays.stream(markers).anyMatch(value::contains);
    }

    private Object parseStructuredResponse(Object raw) {
        if (raw == null) return null;
        try {
            return json.readValue(String.valueOf(raw), Map.class);
        } catch (JsonProcessingException error) {
            return Map.of("parseError", true);
        }
    }

    private void requireOwnedConversation(UUID id) {
        Integer count = jdbc.queryForObject("SELECT COUNT(*) FROM rag_conversations WHERE id=? AND user_id=? AND status='ACTIVE'",
            Integer.class, id, users.currentUserId());
        if (count == null || count == 0) throw new IllegalArgumentException("Conversation not found");
    }

    private void requireOwnedDocuments(UUID userId, List<UUID> pdfIds, List<UUID> noteIds) {
        List<UUID> all = new ArrayList<>(pdfIds);
        all.addAll(noteIds);
        if (all.isEmpty()) return;
        String placeholders = String.join(",", java.util.Collections.nCopies(all.size(), "?"));
        List<Object> params = new ArrayList<>();
        params.add(userId);
        params.addAll(all);
        Integer count = jdbc.queryForObject(
            "SELECT COUNT(DISTINCT id) FROM documents WHERE user_id=? AND id IN (" + placeholders + ")",
            Integer.class, params.toArray());
        if (count == null || count != all.stream().distinct().count()) {
            throw new IllegalArgumentException("One or more scoped documents are unavailable");
        }
    }

    private List<UUID> normalized(List<UUID> ids) {
        return ids == null ? List.of() : ids.stream().filter(java.util.Objects::nonNull).distinct().toList();
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (JsonProcessingException error) {
            throw new IllegalArgumentException("Invalid request data", error);
        }
    }

    private static int estimateTokens(String text) { return Math.max(1, (text.length() + 3) / 4); }
    private static String abbreviatedTitle(String text) { return text.length() <= 80 ? text : text.substring(0, 77) + "..."; }
}
