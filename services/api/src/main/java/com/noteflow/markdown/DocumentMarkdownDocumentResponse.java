package com.noteflow.markdown;

import java.util.UUID;

public record DocumentMarkdownDocumentResponse(
    UUID id,
    UUID documentId,
    String markdown,
    String structureJson,
    String qualityReportJson,
    int offsetChars,
    int totalChars,
    boolean truncated
) {
    public static DocumentMarkdownDocumentResponse from(DocumentMarkdownDocument document) {
        return from(document, Integer.MAX_VALUE);
    }

    public static DocumentMarkdownDocumentResponse from(DocumentMarkdownDocument document, int previewChars) {
        return from(document, 0, previewChars);
    }

    public static DocumentMarkdownDocumentResponse from(DocumentMarkdownDocument document, int offsetChars, int lengthChars) {
        String markdown = document.getMarkdown();
        int total = markdown == null ? 0 : markdown.length();
        int start = Math.max(0, Math.min(offsetChars, total));
        int end = Math.max(start, Math.min(total, start + Math.max(1, lengthChars)));
        String boundedMarkdown = markdown == null ? null : markdown.substring(start, end);
        return new DocumentMarkdownDocumentResponse(
            document.getId(),
            document.getDocumentId(),
            boundedMarkdown,
            document.getStructureJson(),
            document.getQualityReportJson(),
            start,
            total,
            start > 0 || end < total
        );
    }
}
