CREATE TABLE content_addressed_cache (
    namespace VARCHAR(96) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    producer_version VARCHAR(128) NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (namespace, content_hash, producer_version)
);

CREATE INDEX idx_content_addressed_cache_last_accessed
    ON content_addressed_cache(last_accessed_at);

CREATE INDEX idx_document_vlm_results_fingerprint
    ON document_vlm_results(input_fingerprint)
    WHERE input_fingerprint IS NOT NULL AND error_message IS NULL;
