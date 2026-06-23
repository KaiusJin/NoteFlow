package com.noteflow.notes;

import java.time.Instant;
import java.util.UUID;

public record DocumentAiNoteResponse(
    UUID id,
    UUID documentId,
    int noteVersion,
    String status,
    String title,
    String markdown,
    String summary,
    String modelProvider,
    String modelName,
    String promptVersion,
    String qualityReportJson,
    String metadataJson,
    Instant createdAt
) {
    public static DocumentAiNoteResponse from(DocumentAiNote note) {
        return new DocumentAiNoteResponse(
            note.getId(),
            note.getDocumentId(),
            note.getNoteVersion(),
            note.getStatus(),
            note.getTitle(),
            note.getMarkdown(),
            note.getSummary(),
            note.getModelProvider(),
            note.getModelName(),
            note.getPromptVersion(),
            note.getQualityReportJson(),
            note.getMetadataJson(),
            note.getCreatedAt()
        );
    }
}
