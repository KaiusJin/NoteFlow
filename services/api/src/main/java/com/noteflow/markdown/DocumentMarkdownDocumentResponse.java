package com.noteflow.markdown;

import java.util.UUID;

public record DocumentMarkdownDocumentResponse(
    UUID id,
    UUID documentId,
    String markdown,
    String structureJson,
    String qualityReportJson
) {
    public static DocumentMarkdownDocumentResponse from(DocumentMarkdownDocument document) {
        return new DocumentMarkdownDocumentResponse(
            document.getId(),
            document.getDocumentId(),
            document.getMarkdown(),
            document.getStructureJson(),
            document.getQualityReportJson()
        );
    }
}
