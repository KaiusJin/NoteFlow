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
import org.springframework.stereotype.Component;

/**
 * One-time backfill: copies legacy per-document editable notes from
 * {@code document_editable_notes} into the unified {@code notes} table. Idempotent —
 * a document already having a note (by sourceDocumentId) is skipped, so it is
 * safe to run on every startup while the old table lingers.
 */
@Component
@Order(1)
public class LibraryMigrationRunner implements CommandLineRunner {
    private final DocumentEditableNoteRepository legacyNotes;
    private final NoteRepository notes;
    private final DocumentRepository documents;
    private final DocumentMarkdownDocumentRepository markdownDocuments;

    public LibraryMigrationRunner(DocumentEditableNoteRepository legacyNotes, NoteRepository notes,
            DocumentRepository documents, DocumentMarkdownDocumentRepository markdownDocuments) {
        this.legacyNotes = legacyNotes;
        this.notes = notes;
        this.documents = documents;
        this.markdownDocuments = markdownDocuments;
    }

    @Override
    public void run(String... args) {
        backfillLegacyEditableNotes();
        backfillRawMarkdownNotes();
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
