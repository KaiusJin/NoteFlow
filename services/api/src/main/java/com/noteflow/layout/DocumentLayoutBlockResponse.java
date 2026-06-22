package com.noteflow.layout;

import java.util.UUID;

public record DocumentLayoutBlockResponse(
    UUID id,
    UUID documentId,
    int pageNumber,
    int blockIndex,
    String blockType,
    String content,
    String bboxJson,
    String sectionTitle,
    String headingPathJson,
    UUID sourceAssetId,
    Double confidence,
    String metadataJson
) {
    public static DocumentLayoutBlockResponse from(DocumentLayoutBlock block) {
        return new DocumentLayoutBlockResponse(
            block.getId(),
            block.getDocumentId(),
            block.getPageNumber(),
            block.getBlockIndex(),
            block.getBlockType(),
            block.getContent(),
            block.getBboxJson(),
            block.getSectionTitle(),
            block.getHeadingPathJson(),
            block.getSourceAssetId(),
            block.getConfidence(),
            block.getMetadataJson()
        );
    }
}
