package com.noteflow.library;

import java.time.Instant;
import java.util.UUID;

public record NoteResponse(
    UUID id,
    UUID folderId,
    String title,
    String markdown,
    String sourceKind,
    UUID sourceDocumentId,
    Instant createdAt,
    Instant updatedAt
) {
    public static NoteResponse from(Note note) {
        return new NoteResponse(
            note.getId(),
            note.getFolderId(),
            note.getTitle(),
            note.getMarkdown(),
            note.getSourceKind(),
            note.getSourceDocumentId(),
            note.getCreatedAt(),
            note.getUpdatedAt()
        );
    }

    /** Summary without the (potentially large) markdown body, for listings. */
    public static NoteResponse summary(Note note) {
        return new NoteResponse(
            note.getId(),
            note.getFolderId(),
            note.getTitle(),
            null,
            note.getSourceKind(),
            note.getSourceDocumentId(),
            note.getCreatedAt(),
            note.getUpdatedAt()
        );
    }
}
