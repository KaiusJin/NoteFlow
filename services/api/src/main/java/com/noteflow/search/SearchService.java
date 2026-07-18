package com.noteflow.search;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskType;
import com.noteflow.users.DevUserService;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class SearchService {
    private static final int DEFAULT_TOP_K = 8;
    private static final int MAX_TOP_K = 30;

    private final DevUserService users;
    private final DocumentRepository documents;
    private final TaskDispatchService taskDispatcher;
    private final EmbeddingClient embeddingClient;
    private final JdbcTemplate jdbc;

    public SearchService(DevUserService users, DocumentRepository documents, TaskDispatchService taskDispatcher,
            EmbeddingClient embeddingClient, JdbcTemplate jdbc) {
        this.users = users;
        this.documents = documents;
        this.taskDispatcher = taskDispatcher;
        this.embeddingClient = embeddingClient;
        this.jdbc = jdbc;
    }

    @Transactional
    public GenerateEmbeddingsResponse generateEmbeddings(UUID documentId) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        if (document.getStatus() != DocumentStatus.READY) {
            throw new IllegalArgumentException("Document must be READY before generating embeddings");
        }
        Task activeTask = taskDispatcher.latestActiveTask(documentId, TaskType.GENERATE_EMBEDDINGS);
        if (activeTask != null) {
            return new GenerateEmbeddingsResponse(activeTask.getId(), activeTask.getStatus().name());
        }
        Task task = taskDispatcher.createAndEnqueue(documentId, userId, TaskType.GENERATE_EMBEDDINGS);
        return new GenerateEmbeddingsResponse(task.getId(), task.getStatus().name());
    }

    public SearchResponse search(SearchRequest request) {
        UUID userId = users.currentUserId();
        String query = request.query() == null ? "" : request.query().trim();
        if (query.isBlank()) {
            throw new IllegalArgumentException("Search query is required");
        }
        SearchMode mode = request.mode() == null ? SearchMode.MIXED : request.mode();
        int topK = Math.max(1, Math.min(MAX_TOP_K, request.topK() == null ? DEFAULT_TOP_K : request.topK()));
        SearchScope scope = resolveScope(userId, mode, request.pdfDocumentIds(), request.aiNoteDocumentIds());
        if (scope.isEmpty()) {
            return new SearchResponse(query, mode, List.of());
        }

        float[] queryEmbedding = embeddingClient.embed(query);
        int dimension = queryEmbedding.length;
        String vector = vectorLiteral(queryEmbedding);
        String distanceExpression = distanceExpression("embedding", dimension);
        List<Object> params = new ArrayList<>();
        StringBuilder sql = new StringBuilder(
            """
            SELECT
              source_domain,
              source_object_type,
              source_object_id,
              document_id,
              COALESCE((metadata_json::jsonb ->> 'pageStart')::integer, NULL) AS page_start,
              COALESCE((metadata_json::jsonb ->> 'pageEnd')::integer, NULL) AS page_end,
              COALESCE(metadata_json::jsonb ->> 'title', '') AS title,
              COALESCE(text_preview, '') AS snippet,
              metadata_json,
              1 - (%s) AS score
            FROM document_embeddings
            WHERE embedding IS NOT NULL
              AND embedding_provider = ?
              AND embedding_model = ?
              AND embedding_dimension = ?
              AND (
            """.formatted(distanceExpression)
        );
        params.add(vector);
        params.add(embeddingClient.providerName());
        params.add(embeddingClient.model());
        params.add(dimension);

        List<String> domainClauses = new ArrayList<>();
        if (!scope.pdfDocumentIds().isEmpty()) {
            domainClauses.add("source_domain = 'PDF' AND document_id IN (" + placeholders(scope.pdfDocumentIds().size()) + ")");
            params.addAll(scope.pdfDocumentIds());
        }
        if (!scope.aiNoteDocumentIds().isEmpty()) {
            domainClauses.add("source_domain = 'AI_NOTE' AND document_id IN (" + placeholders(scope.aiNoteDocumentIds().size()) + ")");
            params.addAll(scope.aiNoteDocumentIds());
        }
        sql.append(String.join(" OR ", domainClauses));
        sql.append(
            """
              )
            ORDER BY %s
            LIMIT ?
            """.formatted(distanceExpression)
        );
        params.add(vector);
        params.add(topK);

        List<SearchResultResponse> results = jdbc.query(sql.toString(), this::mapResult, params.toArray());
        return new SearchResponse(query, mode, results);
    }

    public SearchResponse searchDocument(UUID documentId, SearchRequest request) {
        SearchMode mode = request.mode() == null ? SearchMode.MIXED : request.mode();
        SearchRequest scoped = switch (mode) {
            case PDF -> new SearchRequest(request.query(), request.topK(), SearchMode.PDF, List.of(documentId), List.of());
            case AI_NOTE -> new SearchRequest(request.query(), request.topK(), SearchMode.AI_NOTE, List.of(), List.of(documentId));
            case CUSTOM -> request;
            case MIXED -> new SearchRequest(request.query(), request.topK(), SearchMode.MIXED, List.of(documentId), List.of(documentId));
        };
        return search(scoped);
    }

    private SearchScope resolveScope(UUID userId, SearchMode mode, List<UUID> pdfDocumentIds, List<UUID> aiNoteDocumentIds) {
        List<UUID> pdfIds = pdfDocumentIds == null ? List.of() : pdfDocumentIds;
        List<UUID> noteIds = aiNoteDocumentIds == null ? List.of() : aiNoteDocumentIds;
        if (mode == SearchMode.CUSTOM) {
            return new SearchScope(filterOwnedReadyDocuments(pdfIds, userId), filterOwnedReadyDocuments(noteIds, userId));
        }
        List<UUID> allReady = documents.findByUserIdOrderByCreatedAtDesc(userId).stream()
            .filter(document -> document.getStatus() == DocumentStatus.READY)
            .map(Document::getId)
            .toList();
        return switch (mode) {
            case PDF -> new SearchScope(allReady, List.of());
            case AI_NOTE -> new SearchScope(List.of(), allReady);
            case MIXED -> new SearchScope(allReady, allReady);
            case CUSTOM -> new SearchScope(filterOwnedReadyDocuments(pdfIds, userId), filterOwnedReadyDocuments(noteIds, userId));
        };
    }

    private List<UUID> filterOwnedReadyDocuments(List<UUID> ids, UUID userId) {
        return ids.stream()
            .distinct()
            .map(id -> loadCurrentUserDocument(id, userId))
            .filter(document -> document.getStatus() == DocumentStatus.READY)
            .map(Document::getId)
            .toList();
    }

    private Document loadCurrentUserDocument(UUID documentId, UUID userId) {
        return documents.findById(documentId)
            .filter(candidate -> candidate.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
    }

    private SearchResultResponse mapResult(ResultSet row, int rowNum) throws SQLException {
        return new SearchResultResponse(
            row.getString("source_domain"),
            row.getString("source_object_type"),
            row.getObject("source_object_id", UUID.class),
            row.getObject("document_id", UUID.class),
            (Integer) row.getObject("page_start"),
            (Integer) row.getObject("page_end"),
            row.getString("title"),
            row.getString("snippet"),
            row.getDouble("score"),
            row.getString("metadata_json")
        );
    }

    private String vectorLiteral(float[] values) {
        StringBuilder builder = new StringBuilder("[");
        for (int index = 0; index < values.length; index++) {
            if (index > 0) {
                builder.append(',');
            }
            builder.append(values[index]);
        }
        return builder.append(']').toString();
    }

    private String placeholders(int count) {
        return String.join(",", java.util.Collections.nCopies(count, "?"));
    }

    private String distanceExpression(String column, int dimension) {
        if (dimension > 2_000 && dimension <= 4_000) {
            return column + "::halfvec(" + dimension + ") <=> ?::halfvec(" + dimension + ")";
        }
        return column + "::vector(" + dimension + ") <=> ?::vector(" + dimension + ")";
    }

    private record SearchScope(List<UUID> pdfDocumentIds, List<UUID> aiNoteDocumentIds) {
        boolean isEmpty() {
            return pdfDocumentIds.isEmpty() && aiNoteDocumentIds.isEmpty();
        }
    }
}
