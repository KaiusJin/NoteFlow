package com.noteflow.editor;

import com.noteflow.library.Note;
import java.time.Instant;
import java.util.UUID;

public record DocumentEditableNoteResponse(
    UUID id,
    UUID documentId,
    String title,
    String markdown,
    String sourceKind,
    Instant createdAt,
    Instant updatedAt
) {
    public static DocumentEditableNoteResponse fromNote(Note note) {
        return new DocumentEditableNoteResponse(
            note.getId(),
            note.getSourceDocumentId(),
            note.getTitle(),
            note.getMarkdown(),
            note.getSourceKind(),
            note.getCreatedAt(),
            note.getUpdatedAt()
        );
    }
}
