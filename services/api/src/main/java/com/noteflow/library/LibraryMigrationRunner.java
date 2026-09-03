package com.noteflow.library;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.editor.DocumentEditableNote;
import com.noteflow.editor.DocumentEditableNoteRepository;
import com.noteflow.markdown.DocumentMarkdownDocument;
import com.noteflow.markdown.DocumentMarkdownDocumentRepository;
import java.util.UUID;
import org.springframework.boot.CommandLineRunner;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * One-time backfill: copies legacy per-document editable notes from
 * {@code document_editable_notes} into the unified {@code notes} table. Idempotent —
 * a document already having a note (by sourceDocumentId) is skipped, so it is
 * safe to run on every startup while the old table lingers.
 *
 * Startup cost is bounded by two COUNT short-circuits: when every legacy row is
 * already migrated the runner returns without scanning. Concurrent instances
 * are serialized by a transaction-scoped advisory lock, and the partial unique
 * index {@code uq_notes_user_source} backstops any unexpected duplicate race.
 */
@Component
@Order(1)
public class LibraryMigrationRunner implements CommandLineRunner {
    private final DocumentEditableNoteRepository legacyNotes;
    private final NoteRepository notes;
    private final DocumentRepository documents;
    private final DocumentMarkdownDocumentRepository markdownDocuments;
    private final JdbcTemplate jdbc;

    public LibraryMigrationRunner(DocumentEditableNoteRepository legacyNotes, NoteRepository notes,
            DocumentRepository documents, DocumentMarkdownDocumentRepository markdownDocuments, JdbcTemplate jdbc) {
        this.legacyNotes = legacyNotes;
        this.notes = notes;
        this.documents = documents;
        this.markdownDocuments = markdownDocuments;
        this.jdbc = jdbc;
    }

    @Override
    @Transactional
    public void run(String... args) {
        if (pendingLegacyEditableNoteCount() == 0 && pendingRawMarkdownNoteCount() == 0) {
            return;
        }
        // Serialize concurrent startups so two instances cannot migrate the
        // same legacy row simultaneously.
        jdbc.queryForObject("SELECT pg_advisory_xact_lock(hashtext(?))", Object.class, "library-migration");
        if (pendingLegacyEditableNoteCount() > 0) {
            backfillLegacyEditableNotes();
        }
        if (pendingRawMarkdownNoteCount() > 0) {
            backfillRawMarkdownNotes();
        }
    }

    private long pendingLegacyEditableNoteCount() {
        Long count = jdbc.queryForObject("""
            SELECT COUNT(*) FROM document_editable_notes l
             WHERE l.document_id IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1 FROM notes n
                     WHERE n.user_id = l.user_id
                       AND n.source_document_id = l.document_id
                       AND n.source_kind = CASE UPPER(COALESCE(NULLIF(BTRIM(l.source_kind), ''), 'BLANK'))
                           WHEN 'RAW' THEN 'RAW'
                           WHEN 'PDF' THEN 'RAW'
                           WHEN 'PDF_MARKDOWN' THEN 'RAW'
                           WHEN 'RAW_MARKDOWN' THEN 'RAW'
                           WHEN 'AI_NOTE' THEN 'AI_NOTE'
                           WHEN 'AI' THEN 'AI_NOTE'
                           WHEN 'AI_NOTES' THEN 'AI_NOTE'
                           WHEN 'IMPORT' THEN 'IMPORT'
                           WHEN 'IMPORTED' THEN 'IMPORT'
                           ELSE 'BLANK'
                       END
               )
            """, Long.class);
        return count == null ? 0 : count;
    }

    private long pendingRawMarkdownNoteCount() {
        Long count = jdbc.queryForObject("""
            SELECT COUNT(*) FROM document_markdown_documents m
             WHERE m.document_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM documents d WHERE d.id = m.document_id)
               AND NOT EXISTS (
                    SELECT 1 FROM notes n
                     WHERE n.source_document_id = m.document_id
                       AND n.source_kind = 'RAW'
               )
            """, Long.class);
        return count == null ? 0 : count;
    }

    private void backfillLegacyEditableNotes() {
        for (DocumentEditableNote legacy : legacyNotes.findAll()) {
            UUID documentId = legacy.getDocumentId();
            if (documentId == null) continue;
            String sourceKind = normalizeSourceKind(legacy.getSourceKind());
            if (notes.findFirstBySourceDocumentIdAndSourceKindOrderByCreatedAtAsc(documentId, sourceKind).isPresent()) {
                continue;
            }
            Note note = new Note(
                UUID.randomUUID(),
                legacy.getUserId(),
                null,
                legacy.getTitle(),
                legacy.getMarkdown(),
                sourceKind,
                documentId
            );
            note.setCreatedAt(legacy.getCreatedAt());
            note.setUpdatedAt(legacy.getUpdatedAt());
            notes.save(note);
        }
    }

    private void backfillRawMarkdownNotes() {
        for (DocumentMarkdownDocument markdownDocument : markdownDocuments.findAll()) {
            UUID documentId = markdownDocument.getDocumentId();
            if (notes.findFirstBySourceDocumentIdAndSourceKindOrderByCreatedAtAsc(documentId, "RAW").isPresent()) {
                continue;
            }
            Document document = documents.findById(documentId).orElse(null);
            if (document == null) continue;
            notes.save(new Note(
                UUID.randomUUID(),
                document.getUserId(),
                null,
                document.getTitle() + " - PDF Markdown",
                markdownDocument.getMarkdown(),
                "RAW",
                documentId
            ));
        }
    }

    private String normalizeSourceKind(String sourceKind) {
        String kind = sourceKind == null || sourceKind.isBlank() ? "BLANK" : sourceKind.trim().toUpperCase();
        return switch (kind) {
            case "RAW", "PDF", "PDF_MARKDOWN", "RAW_MARKDOWN" -> "RAW";
            case "AI_NOTE", "AI", "AI_NOTES" -> "AI_NOTE";
            case "IMPORT", "IMPORTED" -> "IMPORT";
            default -> "BLANK";
        };
    }
}
