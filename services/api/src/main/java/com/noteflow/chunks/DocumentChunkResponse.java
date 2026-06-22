package com.noteflow.chunks;

import java.util.UUID;

public record DocumentChunkResponse(
    UUID id,
    UUID documentId,
    int pageNumber,
    Integer pageStart,
    Integer pageEnd,
    String sectionTitle,
    int chunkIndex,
    String chunkType,
    String content,
    Integer tokenCount,
    UUID sourceAssetId,
    String metadataJson
) {
    public static DocumentChunkResponse from(DocumentChunk chunk) {
        return new DocumentChunkResponse(
            chunk.getId(),
            chunk.getDocumentId(),
            chunk.getPageNumber(),
            chunk.getPageStart(),
            chunk.getPageEnd(),
            chunk.getSectionTitle(),
            chunk.getChunkIndex(),
            chunk.getChunkType(),
            chunk.getContent(),
            chunk.getTokenCount(),
            chunk.getSourceAssetId(),
            chunk.getMetadataJson()
        );
    }
}
