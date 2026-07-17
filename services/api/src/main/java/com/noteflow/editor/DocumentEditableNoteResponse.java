package com.noteflow.editor;

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
    public static DocumentEditableNoteResponse from(DocumentEditableNote note) {
        return new DocumentEditableNoteResponse(
            note.getId(),
            note.getDocumentId(),
            note.getTitle(),
            note.getMarkdown(),
            note.getSourceKind(),
            note.getCreatedAt(),
            note.getUpdatedAt()
        );
    }
}
