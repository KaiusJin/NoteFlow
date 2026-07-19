package com.noteflow.retrieval;

import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class LexicalCandidateRetriever {
    private final JdbcTemplate jdbc;
    private final RetrievalSchemaManager schema;

    LexicalCandidateRetriever(JdbcTemplate jdbc, RetrievalSchemaManager schema) {
        this.jdbc = jdbc;
        this.schema = schema;
    }

    List<RetrievalCandidate> retrieve(QueryAnalysis analysis, RetrievalScope scope, int limit) {
        if (!analysis.hasLexicalQuery() || !schema.ensureReady()) {
            return List.of();
        }
        String lexicalQuery = analysis.lexicalQuery();
        List<Object> params = new ArrayList<>();
        String rankExpression = """
            (
              ts_rank_cd(
                embeddings.search_vector,
                websearch_to_tsquery('simple'::regconfig, ?),
                32
              )
              /
              (
                1 +
                ts_rank_cd(
                  embeddings.search_vector,
                  websearch_to_tsquery('simple'::regconfig, ?),
                  32
                )
              )
            )
            """.trim();
        params.add(lexicalQuery);
        params.add(lexicalQuery);
        StringBuilder inner = new StringBuilder(
            RetrievalCandidateMapper.dedupedSelectAndJoins().formatted(rankExpression)
        );
        inner.append(
            """
            WHERE embeddings.search_vector @@ websearch_to_tsquery('simple'::regconfig, ?)
              AND
            """
        );
        params.add(lexicalQuery);
        inner.append(RetrievalScopeSql.clause(scope, params));
        inner.append(" ORDER BY embeddings.source_object_type, embeddings.source_object_id, channel_score DESC");
        String sql = "SELECT * FROM (" + inner + ") deduped ORDER BY channel_score DESC LIMIT ?";
        params.add(limit);
        return jdbc.query(
            sql,
            (row, rowNum) -> RetrievalCandidateMapper.map(
                row,
                RetrievalChannel.LEXICAL,
                row.getDouble("channel_score")
            ),
            params.toArray()
        );
    }
}
