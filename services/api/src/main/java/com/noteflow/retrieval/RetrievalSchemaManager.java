package com.noteflow.retrieval;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
class RetrievalSchemaManager implements ApplicationRunner {
    private static final Logger log = LoggerFactory.getLogger(RetrievalSchemaManager.class);
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
        ensureVectorIndexes();
        ready.set(true);
        return true;
    }

    @Scheduled(fixedDelayString = "${noteflow.retrieval.hnsw-inspection-millis:300000}")
    void inspectVectorIndexes() {
        if (ready.get()) {
            ensureVectorIndexes();
        }
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
                log.warn("Skipping pgvector HNSW index creation for dimension {}", dimension, error);
            }
        }
    }
}
