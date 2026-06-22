package com.noteflow.assets;

import java.util.UUID;

public record DocumentPageAssetResponse(
    UUID id,
    UUID documentId,
    int pageNumber,
    String assetType,
    String url,
    int width,
    int height,
    int imageCount,
    int drawingCount,
    double imageCoverage,
    int textLength,
    String visualSummary
) {
    public static DocumentPageAssetResponse from(DocumentPageAsset asset) {
        return new DocumentPageAssetResponse(
            asset.getId(),
            asset.getDocumentId(),
            asset.getPageNumber(),
            asset.getAssetType(),
            "/assets/" + asset.getId(),
            asset.getWidth(),
            asset.getHeight(),
            asset.getImageCount(),
            asset.getDrawingCount(),
            asset.getImageCoverage(),
            asset.getTextLength(),
            asset.getVisualSummary()
        );
    }
}
