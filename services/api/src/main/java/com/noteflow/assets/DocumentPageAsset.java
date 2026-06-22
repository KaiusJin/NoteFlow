package com.noteflow.assets;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_page_assets")
public class DocumentPageAsset {
    @Id
    private UUID id;

    private UUID documentId;
    private int pageNumber;
    private String assetType;
    private String imagePath;
    private int width;
    private int height;
    private int imageCount;
    private int drawingCount;
    private double imageCoverage;
    private int textLength;
    @Column(columnDefinition = "TEXT")
    private String visualSummary;
    private Instant createdAt;

    protected DocumentPageAsset() {
    }

    public UUID getId() {
        return id;
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public int getPageNumber() {
        return pageNumber;
    }

    public String getAssetType() {
        return assetType;
    }

    public String getImagePath() {
        return imagePath;
    }

    public int getWidth() {
        return width;
    }

    public int getHeight() {
        return height;
    }

    public int getImageCount() {
        return imageCount;
    }

    public int getDrawingCount() {
        return drawingCount;
    }

    public double getImageCoverage() {
        return imageCoverage;
    }

    public int getTextLength() {
        return textLength;
    }

    public String getVisualSummary() {
        return visualSummary;
    }
}
