package com.noteflow.search;

import java.util.UUID;

public record SearchResultResponse(
    String sourceDomain,
    String sourceObjectType,
    UUID sourceObjectId,
    UUID documentId,
    Integer pageStart,
    Integer pageEnd,
    String title,
    String snippet,
    double score,
    String metadataJson
) {
}
