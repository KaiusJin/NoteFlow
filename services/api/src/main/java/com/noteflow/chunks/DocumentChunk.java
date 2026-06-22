package com.noteflow.chunks;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Column;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_chunks")
public class DocumentChunk {
    @Id
    private UUID id;

    private UUID documentId;
    private int pageNumber;
    private Integer pageStart;
    private Integer pageEnd;
    private String sectionTitle;
    private int chunkIndex;
    private String chunkType;
    @Column(columnDefinition = "TEXT")
    private String content;
    private Integer tokenCount;
    private UUID sourceAssetId;
    @Column(columnDefinition = "TEXT")
    private String metadataJson;
    private Instant createdAt;

    protected DocumentChunk() {
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

    public Integer getPageStart() {
        return pageStart;
    }

    public Integer getPageEnd() {
        return pageEnd;
    }

    public String getSectionTitle() {
        return sectionTitle;
    }

    public int getChunkIndex() {
        return chunkIndex;
    }

    public String getChunkType() {
        return chunkType;
    }

    public String getContent() {
        return content;
    }

    public Integer getTokenCount() {
        return tokenCount;
    }

    public UUID getSourceAssetId() {
        return sourceAssetId;
    }

    public String getMetadataJson() {
        return metadataJson;
    }
}
