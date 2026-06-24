package com.noteflow.retrieval;

import com.noteflow.search.EmbeddingClient;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class ExactSignalCandidateRetriever {
    private final JdbcTemplate jdbc;
    private final EmbeddingClient embeddingClient;
    private final RetrievalSchemaManager schema;

    ExactSignalCandidateRetriever(
        JdbcTemplate jdbc,
        EmbeddingClient embeddingClient,
        RetrievalSchemaManager schema
    ) {
        this.jdbc = jdbc;
        this.embeddingClient = embeddingClient;
        this.schema = schema;
    }

    List<RetrievalCandidate> retrieve(QueryAnalysis analysis, RetrievalScope scope, int limit) {
        if (!analysis.hasExactSignals() || !schema.ensureReady()) {
            return List.of();
        }
        List<Object> params = new ArrayList<>();
        List<String> scoreParts = new ArrayList<>();
        List<String> matchParts = new ArrayList<>();
        for (String signal : analysis.exactSignals()) {
            String escaped = "%" + escapeLike(signal.toLowerCase()) + "%";
            String normalized = "%" + escapeLike(normalizeExactSignal(signal)) + "%";
            scoreParts.add(
                """
                CASE
                  WHEN LOWER(COALESCE(
                    chunks.section_title,
                    note_sections.heading,
                    embeddings.metadata_json::jsonb ->> 'title',
                    ''
                  )) LIKE ? ESCAPE '\\' THEN 2
                  WHEN embeddings.exact_search_text LIKE ? ESCAPE '\\' THEN 1.5
                  WHEN LOWER(COALESCE(chunks.content, note_sections.markdown, embeddings.embedding_text, ''))
                    LIKE ? ESCAPE '\\' THEN 1
                  ELSE 0
                END
                """.trim()
            );
            params.add(escaped);
            params.add(normalized);
            params.add(escaped);
            matchParts.add(
                """
                (
                  LOWER(COALESCE(
                    chunks.section_title,
                    note_sections.heading,
                    embeddings.metadata_json::jsonb ->> 'title',
                    ''
                  )) LIKE ? ESCAPE '\\'
                  OR embeddings.exact_search_text LIKE ? ESCAPE '\\'
                  OR LOWER(COALESCE(chunks.content, note_sections.markdown, embeddings.embedding_text, ''))
                    LIKE ? ESCAPE '\\'
                )
                """.trim()
            );
        }
        double maximumPoints = analysis.exactSignals().size() * 2.0;
        String scoreExpression = "LEAST(1.0, ((" + String.join(" + ", scoreParts)
            + ")::double precision / " + maximumPoints + "))";
        StringBuilder sql = new StringBuilder(
            RetrievalCandidateMapper.selectAndJoins().formatted(scoreExpression)
        );
        sql.append(" WHERE (");
        sql.append(String.join(" OR ", matchParts));
        sql.append(") AND embeddings.embedding_provider = ? AND embeddings.embedding_model = ? AND ");

        for (String signal : analysis.exactSignals()) {
            String escaped = "%" + escapeLike(signal.toLowerCase()) + "%";
            String normalized = "%" + escapeLike(normalizeExactSignal(signal)) + "%";
            params.add(escaped);
            params.add(normalized);
            params.add(escaped);
        }
        params.add(embeddingClient.providerName());
        params.add(embeddingClient.model());
        sql.append(RetrievalScopeSql.clause(scope, params));
        sql.append(" ORDER BY channel_score DESC LIMIT ?");
        params.add(limit);
        return jdbc.query(
            sql.toString(),
            (row, rowNum) -> RetrievalCandidateMapper.map(
                row,
                RetrievalChannel.EXACT,
                row.getDouble("channel_score")
            ),
            params.toArray()
        );
    }

    private String escapeLike(String value) {
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    private String normalizeExactSignal(String value) {
        return Normalizer.normalize(value, Normalizer.Form.NFKC)
            .toLowerCase()
            .replace('[', '(')
            .replace(']', ')')
            .replace('{', '(')
            .replace('}', ')')
            .replaceAll("\\s+", "");
    }
}
