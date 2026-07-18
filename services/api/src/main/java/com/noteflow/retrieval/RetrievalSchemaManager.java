package com.noteflow.retrieval;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.List;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
class RetrievalSchemaManager implements ApplicationRunner {
    private final JdbcTemplate jdbc;
    private final AtomicBoolean ready = new AtomicBoolean(false);

    RetrievalSchemaManager(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public void run(ApplicationArguments args) {
        ensureReady();
    }

    synchronized boolean ensureReady() {
        if (ready.get()) {
            return true;
        }
        Boolean tableExists = jdbc.queryForObject(
            "SELECT to_regclass('public.document_embeddings') IS NOT NULL",
            Boolean.class
        );
        if (!Boolean.TRUE.equals(tableExists)) {
            return false;
        }
        jdbc.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm");
        jdbc.execute(
            """
            ALTER TABLE document_embeddings
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (
              setweight(to_tsvector('simple'::regconfig, COALESCE(embedding_text, '')), 'A') ||
              setweight(to_tsvector('simple'::regconfig, COALESCE(text_preview, '')), 'B')
            ) STORED
            """
        );
        jdbc.execute(
            """
            ALTER TABLE document_embeddings
            ADD COLUMN IF NOT EXISTS exact_search_text TEXT
            GENERATED ALWAYS AS (
              LOWER(
                regexp_replace(
                  translate(COALESCE(embedding_text, ''), '[]{}', '()()'),
                  '[[:space:]]+',
                  '',
                  'g'
                )
              )
            ) STORED
            """
        );
        jdbc.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_embeddings_search_vector
            ON document_embeddings USING GIN (search_vector)
            """
        );
        jdbc.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_embeddings_exact_search
            ON document_embeddings USING GIN (exact_search_text gin_trgm_ops)
            """
        );
        jdbc.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_embeddings_document_domain
            ON document_embeddings(document_id, source_domain)
            """
        );
        jdbc.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_document_embeddings_provider_model
            ON document_embeddings(embedding_provider, embedding_model)
            """
        );
        ensureVectorIndexes();
        ready.set(true);
        return true;
    }

    private void ensureVectorIndexes() {
        List<Integer> dimensions = jdbc.queryForList(
            """
            SELECT DISTINCT embedding_dimension
              FROM document_embeddings
             WHERE embedding IS NOT NULL
               AND embedding_dimension IS NOT NULL
             ORDER BY embedding_dimension
            """,
            Integer.class
        );
        for (Integer dimension : dimensions) {
            if (dimension == null || dimension <= 0 || dimension > 16_384) continue;
            try {
                String indexedExpression = dimension > 2_000 && dimension <= 4_000
                    ? "(embedding::halfvec(" + dimension + ")) halfvec_cosine_ops"
                    : "(embedding::vector(" + dimension + ")) vector_cosine_ops";
                jdbc.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_document_embeddings_embedding_hnsw_%d
                    ON document_embeddings
                    USING hnsw (%s)
                    WHERE embedding IS NOT NULL AND embedding_dimension = %d
                    """.formatted(dimension, indexedExpression, dimension)
                );
            } catch (DataAccessException error) {
                System.out.println("Skipping pgvector HNSW index creation for dimension "
                    + dimension + ": " + error.getMessage());
            }
        }
    }
}
