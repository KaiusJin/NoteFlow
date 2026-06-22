package com.noteflow.layout;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_layout_blocks")
public class DocumentLayoutBlock {
    @Id
    private UUID id;

    private UUID documentId;
    private int pageNumber;
    private int blockIndex;
    private String blockType;
    @Column(columnDefinition = "TEXT")
    private String content;
    @Column(columnDefinition = "TEXT")
    private String bboxJson;
    private String sectionTitle;
    @Column(columnDefinition = "TEXT")
    private String headingPathJson;
    private UUID sourceAssetId;
    private Double confidence;
    @Column(columnDefinition = "TEXT")
    private String metadataJson;
    private Instant createdAt;

    protected DocumentLayoutBlock() {
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

    public int getBlockIndex() {
        return blockIndex;
    }

    public String getBlockType() {
        return blockType;
    }

    public String getContent() {
        return content;
    }

    public String getBboxJson() {
        return bboxJson;
    }

    public String getSectionTitle() {
        return sectionTitle;
    }

    public String getHeadingPathJson() {
        return headingPathJson;
    }

    public UUID getSourceAssetId() {
        return sourceAssetId;
    }

    public Double getConfidence() {
        return confidence;
    }

    public String getMetadataJson() {
        return metadataJson;
    }
}
