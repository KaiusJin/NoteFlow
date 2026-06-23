package com.noteflow.notes;

import java.util.UUID;

public record DocumentAiNoteSectionResponse(
    UUID id,
    UUID noteId,
    UUID documentId,
    int sectionIndex,
    String sectionType,
    String heading,
    String markdown,
    Integer pageStart,
    Integer pageEnd,
    String sourceChunkIdsJson,
    String sourcePagesJson,
    Double confidence,
    String warningsJson,
    String metadataJson
) {
    public static DocumentAiNoteSectionResponse from(DocumentAiNoteSection section) {
        return new DocumentAiNoteSectionResponse(
            section.getId(),
            section.getNoteId(),
            section.getDocumentId(),
            section.getSectionIndex(),
            section.getSectionType(),
            section.getHeading(),
            section.getMarkdown(),
            section.getPageStart(),
            section.getPageEnd(),
            section.getSourceChunkIdsJson(),
            section.getSourcePagesJson(),
            section.getConfidence(),
            section.getWarningsJson(),
            section.getMetadataJson()
        );
    }
}
