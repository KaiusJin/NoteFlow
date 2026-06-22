package com.noteflow.vision;

import java.util.UUID;

public record DocumentVisualRegionResponse(
    UUID id,
    UUID documentId,
    int pageNumber,
    int regionIndex,
    String regionType,
    String url,
    String bboxJson,
    UUID pageAssetId,
    int width,
    int height,
    double confidence,
    String metadataJson
) {
    public static DocumentVisualRegionResponse from(DocumentVisualRegion region) {
        return new DocumentVisualRegionResponse(
            region.getId(),
            region.getDocumentId(),
            region.getPageNumber(),
            region.getRegionIndex(),
            region.getRegionType(),
            "/visual-regions/" + region.getId() + "/asset",
            region.getBboxJson(),
            region.getPageAssetId(),
            region.getWidth(),
            region.getHeight(),
            region.getConfidence(),
            region.getMetadataJson()
        );
    }
}
