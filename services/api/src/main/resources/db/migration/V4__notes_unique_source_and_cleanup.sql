-- Deduplicate notes derived from the same source document (keep the most
-- recently updated row) before enforcing uniqueness, then add a partial
-- unique index so a user can hold at most one note per source document/kind.
-- No tables are dropped.

DELETE FROM notes
 WHERE id IN (
    SELECT id
      FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY user_id, source_document_id, source_kind
                   ORDER BY updated_at DESC, created_at DESC, id DESC
               ) AS rank_in_group
          FROM notes
         WHERE source_document_id IS NOT NULL
       ) ranked
     WHERE ranked.rank_in_group > 1
 );

CREATE UNIQUE INDEX IF NOT EXISTS uq_notes_user_source
    ON notes(user_id, source_document_id, source_kind)
    WHERE source_document_id IS NOT NULL;
