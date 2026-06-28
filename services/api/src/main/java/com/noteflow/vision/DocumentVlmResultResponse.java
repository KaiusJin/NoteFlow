package com.noteflow.vision;

import java.util.UUID;

public record DocumentVlmResultResponse(
    UUID id,
    UUID documentId,
    int pageNumber,
    int regionIndex,
    String regionType,
    String provider,
    String model,
    String transcription,
    String description,
    String latex,
    String code,
    String uncertainty,
    String searchText,
    String contentKind,
    String importance,
    String readingOrder,
    String language,
    int attemptCount,
    String errorMessage
) {
    public static DocumentVlmResultResponse from(DocumentVlmResult result) {
        return new DocumentVlmResultResponse(
            result.getId(),
            result.getDocumentId(),
            result.getPageNumber(),
            result.getRegionIndex(),
            result.getRegionType(),
            result.getProvider(),
            result.getModel(),
            result.getTranscription(),
            result.getDescription(),
            result.getLatex(),
            result.getCode(),
            result.getUncertainty(),
            result.getSearchText(),
            result.getContentKind(),
            result.getImportance(),
            result.getReadingOrder(),
            result.getLanguage(),
            result.getAttemptCount(),
            result.getErrorMessage()
        );
    }
}
