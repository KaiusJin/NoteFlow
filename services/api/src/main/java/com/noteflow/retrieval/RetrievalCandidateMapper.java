package com.noteflow.retrieval;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;

final class RetrievalCandidateMapper {
    private RetrievalCandidateMapper() {
    }

    static RetrievalCandidate map(ResultSet row, RetrievalChannel channel, double score) throws SQLException {
        Double vectorScore = channel == RetrievalChannel.VECTOR ? score : null;
        Double lexicalScore = channel == RetrievalChannel.LEXICAL ? score : null;
        Double exactScore = channel == RetrievalChannel.EXACT ? score : null;
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
            score,
            vectorScore,
            lexicalScore,
            exactScore,
            0,
            List.of(channel.name())
        );
    }

    /**
     * Variant for channels that read provider-independent columns
     * (search_vector, exact_search_text). Multiple embedding providers may
     * store one row each per source object, so those channels deduplicate by
     * source instead of filtering on the active embedding provider — the old
     * provider filter silently emptied lexical/exact recall whenever the
     * configured provider did not match the rows (e.g. provider disabled).
     * Requires ORDER BY to lead with source_object_type, source_object_id.
     */
    static String dedupedSelectAndJoins() {
        return selectAndJoins().replaceFirst(
            "SELECT",
            "SELECT DISTINCT ON (embeddings.source_object_type, embeddings.source_object_id)"
        );
    }

    static String selectAndJoins() {
        return """
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
              %s AS channel_score
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
            """;
    }
}
