package com.noteflow.conversations;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.users.DevUserService;
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
    private final DevUserService users;
    private final TaskDispatchService tasks;

    public ConversationService(JdbcTemplate jdbc, ObjectMapper json, DevUserService users, TaskDispatchService tasks) {
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
        requireOwnedConversation(conversationId);
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> row : jdbc.queryForList("""
            SELECT id,role,status,content_markdown,model_provider,model_name,error_message,created_at,completed_at
              FROM rag_messages WHERE conversation_id=? ORDER BY created_at,id
            """, conversationId)) {
            result.add(withCitations(row));
        }
        return result;
    }

    public Map<String, Object> message(UUID messageId) {
        Map<String, Object> row = jdbc.queryForMap("""
            SELECT m.id,m.conversation_id,m.role,m.status,m.content_markdown,m.model_provider,m.model_name,
                   m.error_message,m.created_at,m.completed_at
              FROM rag_messages m JOIN rag_conversations c ON c.id=m.conversation_id
             WHERE m.id=? AND c.user_id=?
            """, messageId, users.currentUserId());
        return withCitations(row);
    }

    @Transactional
    public Map<String, Object> send(UUID conversationId, String content, List<UUID> pdfIds, List<UUID> aiNoteIds) {
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
        jdbc.update("""
            INSERT INTO rag_messages(id,conversation_id,role,status,content_markdown,token_count,completed_at)
            VALUES (?,?, 'USER','COMPLETED',?,?,NOW())
            """, userMessageId, conversationId, text, estimateTokens(text));
        jdbc.update("""
            INSERT INTO rag_messages(id,conversation_id,role,status,content_markdown,metadata_json)
            VALUES (?,?, 'ASSISTANT','GENERATING','',?)
            """, assistantMessageId, conversationId, toJson(Map.of("userMessageId", userMessageId.toString())));
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

    private Map<String, Object> withCitations(Map<String, Object> row) {
        LinkedHashMap<String, Object> result = new LinkedHashMap<>(row);
        result.put("citations", jdbc.queryForList("""
            SELECT citation_index,document_id,source_title AS document_title,page_start,page_end,
                   evidence_snapshot AS quote_text,retrieval_score AS similarity_score
              FROM rag_message_citations WHERE message_id=? ORDER BY citation_index
            """, row.get("id")));
        return result;
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
