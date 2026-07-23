package com.noteflow.study;

import com.noteflow.documents.Document;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskType;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class FlashcardGenerationService {
    private final StudyGenerationSupport support;
    private final TaskDispatchService tasks;

    public FlashcardGenerationService(StudyGenerationSupport support, TaskDispatchService tasks) {
        this.support = support;
        this.tasks = tasks;
    }

    @Transactional
    public Map<String, Object> generate(FlashcardGenerationRequest request) {
        List<Document> sources = support.readyDocuments(request.documentIds());
        Map<String, Object> scope = support.scope(sources, request.sourceChunkIds(), request.section(), request.focus());
        String origin = StudyGenerationSupport.origin(request.origin());
        Integer count = request.count();
        if (count != null && (count < 1 || count > 500)) throw new IllegalArgumentException("Flashcard count must be between 1 and 500");
        LinkedHashMap<String, Object> options = new LinkedHashMap<>();
        if (count != null) options.put("targetCount", count);
        options.put("groupBySection", !Boolean.FALSE.equals(request.groupBySection()));
        if (scope.containsKey("focus")) options.put("focus", scope.get("focus"));
        UUID primary = sources.get(0).getId();
        String scopeJson = support.json(scope);
        String optionsJson = support.json(options);
        var active = support.jdbc().queryForList("""
            SELECT id,status,version,title FROM flashcard_decks
             WHERE document_id=? AND user_id=? AND origin=? AND source_scope_json=?
               AND generation_options_json=? AND status IN ('GENERATING','PARTIAL')
             ORDER BY version DESC LIMIT 1
            """, primary, support.workspaceId(), origin, scopeJson, optionsJson);
        if (!active.isEmpty()) {
            var row = active.get(0);
            UUID deckId = (UUID) row.get("id");
            if ("GENERATING".equals(row.get("status"))) {
                UUID activeTaskId = support.activeTaskId(deckId);
                if (activeTaskId != null) return result(primary, deckId, activeTaskId, ((Number) row.get("version")).intValue(), true, String.valueOf(row.get("title")));
            } else {
                support.jdbc().update("UPDATE flashcard_decks SET status='GENERATING',error_message=NULL,updated_at=NOW() WHERE id=?", deckId);
            }
            return enqueue(primary, deckId, ((Number) row.get("version")).intValue(), true, String.valueOf(row.get("title")));
        }
        int version = support.jdbc().queryForObject("SELECT COALESCE(MAX(version),0)+1 FROM flashcard_decks WHERE document_id=?", Integer.class, primary);
        UUID id = UUID.randomUUID();
        String title = support.title(request.title(), sources, scope, "Flashcards");
        support.jdbc().update("""
            INSERT INTO flashcard_decks(id,document_id,user_id,version,title,status,generation_options_json,origin,source_scope_json)
            VALUES (?,?,?,?,?,'GENERATING',?,?,?)
            """, id, primary, support.workspaceId(), version, title, optionsJson, origin, scopeJson);
        return enqueue(primary, id, version, false, title);
    }

    private Map<String, Object> enqueue(UUID documentId, UUID deckId, int version, boolean reused, String title) {
        Task task = tasks.createAndEnqueue(documentId, support.workspaceId(), TaskType.GENERATE_FLASHCARDS);
        support.bindTask(task.getId(), deckId);
        return result(documentId, deckId, task.getId(), version, reused, title);
    }

    private Map<String, Object> result(UUID documentId, UUID deckId, UUID taskId, int version, boolean reused, String title) {
        return Map.of("deckId", deckId, "taskId", taskId, "status", "GENERATING", "version", version,
            "reused", reused, "title", title, "artifactUrl", "/study/flashcards/" + deckId,
            "kind", "flashcards", "documentId", documentId);
    }
}
