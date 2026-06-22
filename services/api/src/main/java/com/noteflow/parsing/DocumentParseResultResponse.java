package com.noteflow.parsing;

import com.noteflow.documents.ContentSourceType;
import java.time.Instant;
import java.util.UUID;

public record DocumentParseResultResponse(
    UUID documentId,
    String parserName,
    int pageCount,
    int extractedTextLength,
    String extractedTextPreview,
    ContentSourceType detectedContentSourceType,
    Instant createdAt
) {
    public static DocumentParseResultResponse from(DocumentParseResult result) {
        return new DocumentParseResultResponse(
            result.getDocumentId(),
            result.getParserName(),
            result.getPageCount(),
            result.getExtractedTextLength(),
            result.getExtractedTextPreview(),
            result.getDetectedContentSourceType(),
            result.getCreatedAt()
        );
    }
}
