package com.noteflow.vision;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_visual_regions")
public class DocumentVisualRegion {
    @Id
    private UUID id;

    private UUID documentId;
    private int pageNumber;
    private int regionIndex;
    private String regionType;
    private String assetPath;
    @Column(columnDefinition = "TEXT")
    private String bboxJson;
    private UUID pageAssetId;
    private int width;
    private int height;
    private double confidence;
    @Column(columnDefinition = "TEXT")
    private String metadataJson;
    private Instant createdAt;

    protected DocumentVisualRegion() {
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

    public int getRegionIndex() {
        return regionIndex;
    }

    public String getRegionType() {
        return regionType;
    }

    public String getAssetPath() {
        return assetPath;
    }

    public String getBboxJson() {
        return bboxJson;
    }

    public UUID getPageAssetId() {
        return pageAssetId;
    }

    public int getWidth() {
        return width;
    }

    public int getHeight() {
        return height;
    }

    public double getConfidence() {
        return confidence;
    }

    public String getMetadataJson() {
        return metadataJson;
    }
}
