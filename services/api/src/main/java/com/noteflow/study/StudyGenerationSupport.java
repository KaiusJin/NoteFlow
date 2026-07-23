package com.noteflow.study;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class StudyGenerationSupport {
    private final DocumentRepository documents;
    private final LocalWorkspaceService workspace;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;

    StudyGenerationSupport(DocumentRepository documents, LocalWorkspaceService workspace, JdbcTemplate jdbc, ObjectMapper json) {
        this.documents = documents;
        this.workspace = workspace;
        this.jdbc = jdbc;
        this.json = json;
    }

    UUID workspaceId() { return workspace.currentWorkspaceId(); }
    JdbcTemplate jdbc() { return jdbc; }

    List<Document> readyDocuments(List<UUID> rawIds) {
        List<UUID> ids = rawIds == null ? List.of() : rawIds.stream().filter(java.util.Objects::nonNull).distinct().toList();
        if (ids.isEmpty()) throw new IllegalArgumentException("Select at least one source document");
        if (ids.size() > 8) throw new IllegalArgumentException("A generation may cover at most 8 documents");
        return ids.stream().map(id -> documents.findById(id)
            .filter(document -> document.getUserId().equals(workspaceId()))
            .filter(document -> document.getStatus() == DocumentStatus.READY)
            .orElseThrow(() -> new IllegalArgumentException("Document is unavailable or not READY: " + id)))
            .toList();
    }

    Map<String, Object> scope(List<Document> sources, List<UUID> chunks, String section, String focus) {
        LinkedHashMap<String, Object> scope = new LinkedHashMap<>();
        List<UUID> documentIds = sources.stream().map(Document::getId).toList();
        scope.put("documentIds", documentIds);
        List<UUID> chunkIds = chunks == null ? List.of() : chunks.stream().filter(java.util.Objects::nonNull).distinct().limit(200).toList();
        if (!chunkIds.isEmpty()) {
            String placeholders = String.join(",", java.util.Collections.nCopies(chunkIds.size(), "?"));
            java.util.ArrayList<Object> params = new java.util.ArrayList<>(documentIds);
            params.addAll(chunkIds);
            String documentPlaceholders = String.join(",", java.util.Collections.nCopies(documentIds.size(), "?"));
            Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM document_chunks WHERE document_id IN (" + documentPlaceholders + ") AND id IN (" + placeholders + ")",
                Integer.class, params.toArray());
            if (count == null || count != chunkIds.size()) throw new IllegalArgumentException("One or more source chunks are outside the selected documents");
            scope.put("chunkIds", chunkIds);
        }
        if (section != null && !section.isBlank()) scope.put("sectionQuery", section.trim().substring(0, Math.min(300, section.trim().length())));
        if (focus != null && !focus.isBlank()) scope.put("focus", focus.trim().substring(0, Math.min(500, focus.trim().length())));
        return scope;
    }

    String title(String requested, List<Document> sources, Map<String, Object> scope, String suffix) {
        if (requested != null && !requested.isBlank()) return requested.trim().substring(0, Math.min(300, requested.trim().length()));
        String label = sources.stream().limit(3).map(Document::getTitle).reduce((a, b) -> a + " + " + b).orElse("Sources");
        if (sources.size() > 3) label += " +" + (sources.size() - 3) + " more";
        Object detail = scope.getOrDefault("focus", scope.get("sectionQuery"));
        return detail == null ? label + " - " + suffix : label + " - " + detail;
    }

    String json(Object value) {
        try { return json.writeValueAsString(value); }
        catch (JsonProcessingException error) { throw new IllegalArgumentException("Invalid generation configuration", error); }
    }

    void bindTask(UUID taskId, UUID targetId) {
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS study_task_targets (
              task_id UUID PRIMARY KEY, attempt_id UUID, target_id UUID,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())
            """);
        jdbc.update("INSERT INTO study_task_targets(task_id,target_id) VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET target_id=EXCLUDED.target_id",
            taskId, targetId);
    }

    UUID activeTaskId(UUID targetId) {
        try {
            List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT t.id FROM study_task_targets x JOIN tasks t ON t.id=x.task_id
                 WHERE x.target_id=? AND t.status IN ('PENDING','PROCESSING','RETRYING')
                 ORDER BY t.created_at DESC LIMIT 1
                """, targetId);
            return rows.isEmpty() ? null : (UUID) rows.get(0).get("id");
        } catch (org.springframework.dao.DataAccessException missingCompatibilityTable) {
            return null;
        }
    }

    static String origin(String value) { return "AGENT".equalsIgnoreCase(value) ? "AGENT" : "SECTION"; }
}
