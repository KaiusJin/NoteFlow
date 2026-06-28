package com.noteflow.parsing;

import com.noteflow.documents.ContentSourceType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_parse_results")
public class DocumentParseResult {
    @Id
    private UUID id;

    private UUID documentId;
    private String parserName;
    private int pageCount;
    private int extractedTextLength;
    @Column(columnDefinition = "TEXT")
    private String extractedTextPreview;

    @Enumerated(EnumType.STRING)
    private ContentSourceType detectedContentSourceType;
    private Double sourceConfidence;
    @Column(columnDefinition = "TEXT")
    private String sourceDistributionJson;

    private Instant createdAt;
    private Instant updatedAt;

    protected DocumentParseResult() {
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public String getParserName() {
        return parserName;
    }

    public int getPageCount() {
        return pageCount;
    }

    public int getExtractedTextLength() {
        return extractedTextLength;
    }

    public String getExtractedTextPreview() {
        return extractedTextPreview;
    }

    public ContentSourceType getDetectedContentSourceType() {
        return detectedContentSourceType;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Double getSourceConfidence() {
        return sourceConfidence;
    }

    public String getSourceDistributionJson() {
        return sourceDistributionJson;
    }
}
