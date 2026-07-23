package com.noteflow.study;

import com.noteflow.documents.Document;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskType;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class QuizGenerationService {
    private static final int MAX_QUESTIONS = 60;
    private static final List<String> DEFAULT_TYPES = List.of("MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER");
    private static final Set<String> TYPES = Set.copyOf(DEFAULT_TYPES);
    private final StudyGenerationSupport support;
    private final TaskDispatchService tasks;

    public QuizGenerationService(StudyGenerationSupport support, TaskDispatchService tasks) {
        this.support = support;
        this.tasks = tasks;
    }

    @Transactional
    public Map<String, Object> generate(QuizGenerationRequest request) {
        List<Document> sources = support.readyDocuments(request.documentIds());
        Map<String, Object> scope = support.scope(sources, request.sourceChunkIds(), request.section(), request.focus());
        String origin = StudyGenerationSupport.origin(request.origin());
        int easy = count(request.easy(), origin.equals("AGENT") ? 3 : 0, "easy");
        int medium = count(request.medium(), origin.equals("AGENT") ? 5 : 0, "medium");
        int hard = count(request.hard(), origin.equals("AGENT") ? 2 : 0, "hard");
        if (easy + medium + hard < 1) throw new IllegalArgumentException("Choose at least one question");
        if (easy + medium + hard > MAX_QUESTIONS) throw new IllegalArgumentException("A quiz may contain at most 60 questions");
        List<String> types = normalizeTypes(request.questionTypes());
        Map<String, Integer> counts = Map.of("EASY", easy, "MEDIUM", medium, "HARD", hard);
        LinkedHashMap<String, Object> options = new LinkedHashMap<>();
        options.put("difficultyCounts", counts);
        options.put("totalQuestions", easy + medium + hard);
        options.put("questionTypes", types);
        options.put("includeExplanations", !Boolean.FALSE.equals(request.includeExplanations()));
        if (scope.containsKey("focus")) options.put("focus", scope.get("focus"));
        UUID primary = sources.get(0).getId();
        String scopeJson = support.json(scope);
        var active = support.jdbc().queryForList("""
            SELECT id,status,version,title FROM quiz_sets
             WHERE document_id=? AND user_id=? AND origin=? AND source_scope_json=?
               AND generation_options_json=? AND status IN ('GENERATING','PARTIAL')
             ORDER BY version DESC LIMIT 1
            """, primary, support.workspaceId(), origin, scopeJson, support.json(options));
        if (!active.isEmpty()) {
            var row = active.get(0);
            UUID setId = (UUID) row.get("id");
            if ("GENERATING".equals(row.get("status"))) {
                UUID activeTaskId = support.activeTaskId(setId);
                if (activeTaskId != null) return result(primary, setId, activeTaskId, ((Number) row.get("version")).intValue(), easy + medium + hard, true, String.valueOf(row.get("title")));
            } else {
                support.jdbc().update("UPDATE quiz_sets SET status='GENERATING',error_message=NULL,updated_at=NOW() WHERE id=?", setId);
            }
            return enqueue(primary, setId, ((Number) row.get("version")).intValue(), easy + medium + hard, true, String.valueOf(row.get("title")));
        }
        int version = support.jdbc().queryForObject("SELECT COALESCE(MAX(version),0)+1 FROM quiz_sets WHERE document_id=?", Integer.class, primary);
        UUID id = UUID.randomUUID();
        String title = support.title(request.title(), sources, scope, "Quiz");
        support.jdbc().update("""
            INSERT INTO quiz_sets(id,document_id,user_id,version,title,status,difficulty_distribution_json,
                                  generation_options_json,origin,source_scope_json)
            VALUES (?,?,?,?,?,'GENERATING',?,?,?,?)
            """, id, primary, support.workspaceId(), version, title, support.json(counts), support.json(options), origin, scopeJson);
        return enqueue(primary, id, version, easy + medium + hard, false, title);
    }

    private Map<String, Object> enqueue(UUID documentId, UUID setId, int version, int total, boolean reused, String title) {
        Task task = tasks.createAndEnqueue(documentId, support.workspaceId(), TaskType.GENERATE_QUIZ);
        support.bindTask(task.getId(), setId);
        return result(documentId, setId, task.getId(), version, total, reused, title);
    }

    private Map<String, Object> result(UUID documentId, UUID setId, UUID taskId, int version, int total, boolean reused, String title) {
        return Map.of("quizSetId", setId, "taskId", taskId, "status", "GENERATING", "version", version,
            "requestedTotal", total, "reused", reused, "title", title, "artifactUrl", "/study/quizzes/" + setId,
            "kind", "quiz", "documentId", documentId);
    }

    private static int count(Integer value, int fallback, String name) {
        int result = value == null ? fallback : value;
        if (result < 0 || result > MAX_QUESTIONS) throw new IllegalArgumentException(name + " count must be between 0 and 60");
        return result;
    }

    private static List<String> normalizeTypes(List<String> raw) {
        if (raw == null || raw.isEmpty()) return DEFAULT_TYPES;
        List<String> normalized = raw.stream().filter(java.util.Objects::nonNull).map(value -> value.trim().toUpperCase(Locale.ROOT)).distinct().toList();
        if (normalized.isEmpty() || !TYPES.containsAll(normalized)) throw new IllegalArgumentException("Unsupported quiz question type");
        return normalized;
    }
}
