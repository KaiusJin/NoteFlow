package com.noteflow.retrieval;

import com.noteflow.search.EmbeddingClient;
import java.util.ArrayList;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class LexicalCandidateRetriever {
    private final JdbcTemplate jdbc;
    private final EmbeddingClient embeddingClient;
    private final RetrievalSchemaManager schema;

    LexicalCandidateRetriever(
        JdbcTemplate jdbc,
        EmbeddingClient embeddingClient,
        RetrievalSchemaManager schema
    ) {
        this.jdbc = jdbc;
        this.embeddingClient = embeddingClient;
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
        StringBuilder sql = new StringBuilder(
            RetrievalCandidateMapper.selectAndJoins().formatted(rankExpression)
        );
        sql.append(
            """
            WHERE embeddings.search_vector @@ websearch_to_tsquery('simple'::regconfig, ?)
              AND embeddings.embedding_provider = ?
              AND embeddings.embedding_model = ?
              AND
            """
        );
        params.add(lexicalQuery);
        params.add(embeddingClient.providerName());
        params.add(embeddingClient.model());
        sql.append(RetrievalScopeSql.clause(scope, params));
        sql.append(" ORDER BY channel_score DESC LIMIT ?");
        params.add(limit);
        return jdbc.query(
            sql.toString(),
            (row, rowNum) -> RetrievalCandidateMapper.map(
                row,
                RetrievalChannel.LEXICAL,
                row.getDouble("channel_score")
            ),
            params.toArray()
        );
    }
}
