package com.noteflow.markdown;

import java.util.UUID;

public record DocumentMarkdownPageResponse(
    UUID id,
    UUID documentId,
    int pageNumber,
    String markdown,
    String sourceType,
    double qualityScore,
    String warningsJson,
    String structureJson
) {
    public static DocumentMarkdownPageResponse from(DocumentMarkdownPage page) {
        return new DocumentMarkdownPageResponse(
            page.getId(),
            page.getDocumentId(),
            page.getPageNumber(),
            page.getMarkdown(),
            page.getSourceType(),
            page.getQualityScore(),
            page.getWarningsJson(),
            page.getStructureJson()
        );
    }
}
