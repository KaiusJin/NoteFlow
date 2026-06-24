package com.noteflow.retrieval;

import java.util.List;
import java.util.UUID;

public record RetrievalItemResponse(
    String citationId,
    String sourceDomain,
    String sourceObjectType,
    UUID documentId,
    String documentTitle,
    Integer pageStart,
    Integer pageEnd,
    List<UUID> sourceObjectIds,
    String title,
    String content,
    int tokenCount,
    double score,
    Double vectorScore,
    Double lexicalScore,
    Double exactScore,
    double fusionScore,
    List<String> matchedChannels,
    boolean truncated
) {
}
