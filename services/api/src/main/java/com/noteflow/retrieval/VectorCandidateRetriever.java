package com.noteflow.retrieval;

import com.noteflow.search.EmbeddingClient;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class VectorCandidateRetriever {
    private final EmbeddingClient embeddingClient;
    private final JdbcTemplate jdbc;

    VectorCandidateRetriever(EmbeddingClient embeddingClient, JdbcTemplate jdbc) {
        this.embeddingClient = embeddingClient;
        this.jdbc = jdbc;
    }

    List<RetrievalCandidate> retrieve(String query, RetrievalScope scope, int limit) {
        return retrieve(query, null, scope, limit);
    }

    List<RetrievalCandidate> retrieve(
        String query,
        String hypotheticalDocument,
        RetrievalScope scope,
        int limit
    ) {
        List<RetrievalCandidate> original = retrieveSingle(query, scope, limit);
        if (hypotheticalDocument == null || hypotheticalDocument.isBlank()) {
            return original;
        }
        List<RetrievalCandidate> hypothetical = retrieveSingle(hypotheticalDocument, scope, limit);
        java.util.Map<java.util.UUID, RetrievalCandidate> combined = new java.util.LinkedHashMap<>();
        for (RetrievalCandidate candidate : original) {
            combined.put(candidate.sourceObjectId(), candidate);
        }
        for (RetrievalCandidate candidate : hypothetical) {
            RetrievalCandidate weighted = candidate.withScores(
                candidate.score() * 0.90,
                null,
                null,
                0,
                List.of("VECTOR")
            );
            combined.merge(
                candidate.sourceObjectId(),
                weighted,
                (left, right) -> left.score() >= right.score() ? left : right
            );
        }
        return combined.values().stream()
            .sorted(java.util.Comparator.comparingDouble(RetrievalCandidate::score).reversed())
            .limit(limit)
            .toList();
    }

    private List<RetrievalCandidate> retrieveSingle(String query, RetrievalScope scope, int limit) {
        float[] queryEmbedding = embeddingClient.embed(query);
        int dimension = queryEmbedding.length;
        String vector = vectorLiteral(queryEmbedding);
        String distanceExpression = distanceExpression("embeddings.embedding", dimension);
        List<Object> params = new ArrayList<>();
        StringBuilder sql = new StringBuilder(
            """
            SELECT
              embeddings.source_domain,
              embeddings.source_object_type,
              embeddings.source_object_id,
              embeddings.document_id,
              documents.title AS document_title,
              COALESCE(
                chunks.page_start,
                note_sections.page_start,
                (embeddings.metadata_json::jsonb ->> 'pageStart')::integer
              ) AS page_start,
              COALESCE(
                chunks.page_end,
                note_sections.page_end,
                (embeddings.metadata_json::jsonb ->> 'pageEnd')::integer
              ) AS page_end,
              COALESCE(
                chunks.section_title,
                note_sections.heading,
                embeddings.metadata_json::jsonb ->> 'title',
                ''
              ) AS title,
              COALESCE(chunks.content, note_sections.markdown, embeddings.embedding_text, '') AS content,
              chunks.chunk_index,
              COALESCE(
                chunks.token_count,
                (embeddings.metadata_json::jsonb ->> 'tokenCount')::integer
              ) AS token_count,
              1 - (%s) AS score
            FROM document_embeddings embeddings
            JOIN documents ON documents.id = embeddings.document_id
            LEFT JOIN document_chunks chunks
              ON embeddings.source_object_type = 'DOCUMENT_CHUNK'
             AND chunks.id = embeddings.source_object_id
            LEFT JOIN document_ai_note_sections note_sections
              ON embeddings.source_object_type = 'AI_NOTE_SECTION'
             AND note_sections.id = embeddings.source_object_id
            LEFT JOIN document_ai_notes notes
              ON notes.id = note_sections.note_id
            WHERE embeddings.embedding IS NOT NULL
              AND embeddings.embedding_provider = ?
              AND embeddings.embedding_model = ?
              AND embeddings.embedding_dimension = ?
              AND (
            """.formatted(distanceExpression)
        );
        params.add(vector);
        params.add(embeddingClient.providerName());
        params.add(embeddingClient.model());
        params.add(dimension);

        List<String> domainClauses = new ArrayList<>();
        if (!scope.pdfDocumentIds().isEmpty()) {
            domainClauses.add(
                "embeddings.source_domain = 'PDF' AND embeddings.document_id IN ("
                    + placeholders(scope.pdfDocumentIds().size()) + ")"
            );
            params.addAll(scope.pdfDocumentIds());
        }
        if (!scope.aiNoteDocumentIds().isEmpty()) {
            domainClauses.add(
                """
                embeddings.source_domain = 'AI_NOTE'
                AND embeddings.document_id IN (%s)
                AND notes.status = 'READY'
                AND notes.note_version = (
                  SELECT MAX(latest.note_version)
                  FROM document_ai_notes latest
                  WHERE latest.document_id = embeddings.document_id
                    AND latest.status = 'READY'
                )
                """.formatted(placeholders(scope.aiNoteDocumentIds().size())).trim()
            );
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
        params.add(limit);
        return jdbc.query(sql.toString(), this::mapCandidate, params.toArray());
    }

    private RetrievalCandidate mapCandidate(ResultSet row, int rowNum) throws SQLException {
        return new RetrievalCandidate(
            row.getString("source_domain"),
            row.getString("source_object_type"),
            row.getObject("source_object_id", java.util.UUID.class),
            row.getObject("document_id", java.util.UUID.class),
            row.getString("document_title"),
            (Integer) row.getObject("page_start"),
            (Integer) row.getObject("page_end"),
            row.getString("title"),
            row.getString("content"),
            (Integer) row.getObject("chunk_index"),
            (Integer) row.getObject("token_count"),
            row.getDouble("score")
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
}
