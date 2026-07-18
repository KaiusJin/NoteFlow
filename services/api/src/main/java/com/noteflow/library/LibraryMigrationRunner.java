package com.noteflow.library;

import com.noteflow.editor.DocumentEditableNote;
import com.noteflow.editor.DocumentEditableNoteRepository;
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

    public LibraryMigrationRunner(DocumentEditableNoteRepository legacyNotes, NoteRepository notes) {
        this.legacyNotes = legacyNotes;
        this.notes = notes;
    }

    @Override
    public void run(String... args) {
        for (DocumentEditableNote legacy : legacyNotes.findAll()) {
            UUID documentId = legacy.getDocumentId();
            if (documentId == null) continue;
            if (notes.findFirstBySourceDocumentIdOrderByUpdatedAtDesc(documentId).isPresent()) continue;
            Note note = new Note(
                UUID.randomUUID(),
                legacy.getUserId(),
                null,
                legacy.getTitle(),
                legacy.getMarkdown(),
                normalizeSourceKind(legacy.getSourceKind()),
                documentId
            );
            note.setCreatedAt(legacy.getCreatedAt());
            note.setUpdatedAt(legacy.getUpdatedAt());
            notes.save(note);
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
