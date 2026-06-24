CREATE EXTENSION IF NOT EXISTS pg_trgm;

DO $$
BEGIN
  IF to_regclass('public.document_embeddings') IS NOT NULL THEN
    ALTER TABLE document_embeddings
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
      setweight(to_tsvector('simple'::regconfig, COALESCE(embedding_text, '')), 'A') ||
      setweight(to_tsvector('simple'::regconfig, COALESCE(text_preview, '')), 'B')
    ) STORED;

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
    ) STORED;

    CREATE INDEX IF NOT EXISTS idx_document_embeddings_search_vector
    ON document_embeddings USING GIN (search_vector);

    CREATE INDEX IF NOT EXISTS idx_document_embeddings_exact_search
    ON document_embeddings USING GIN (exact_search_text gin_trgm_ops);

    CREATE INDEX IF NOT EXISTS idx_document_embeddings_document_domain
    ON document_embeddings(document_id, source_domain);

    CREATE INDEX IF NOT EXISTS idx_document_embeddings_provider_model
    ON document_embeddings(embedding_provider, embedding_model);
  END IF;
END $$;
