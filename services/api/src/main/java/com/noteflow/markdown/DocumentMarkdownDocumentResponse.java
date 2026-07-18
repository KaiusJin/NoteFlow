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
        return from(document, Integer.MAX_VALUE);
    }

    public static DocumentMarkdownDocumentResponse from(DocumentMarkdownDocument document, int previewChars) {
        String markdown = document.getMarkdown();
        String boundedMarkdown = markdown == null || markdown.length() <= previewChars
            ? markdown
            : markdown.substring(0, previewChars);
        return new DocumentMarkdownDocumentResponse(
            document.getId(),
            document.getDocumentId(),
            boundedMarkdown,
            document.getStructureJson(),
            document.getQualityReportJson()
        );
    }
}
