package com.noteflow.vision;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_vlm_results")
public class DocumentVlmResult {
    @Id
    private UUID id;

    private UUID documentId;
    private int pageNumber;
    private int regionIndex;
    private String regionType;
    private String provider;
    private String model;
    @Column(columnDefinition = "TEXT")
    private String transcription;
    @Column(columnDefinition = "TEXT")
    private String description;
    @Column(columnDefinition = "TEXT")
    private String latex;
    @Column(columnDefinition = "TEXT")
    private String code;
    @Column(columnDefinition = "TEXT")
    private String uncertainty;
    @Column(columnDefinition = "TEXT")
    private String searchText;
    private String contentKind;
    private String importance;
    @Column(columnDefinition = "TEXT")
    private String readingOrder;
    private String language;
    private int attemptCount;
    @Column(columnDefinition = "TEXT")
    private String errorMessage;
    private Instant createdAt;

    protected DocumentVlmResult() {
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

    public String getProvider() {
        return provider;
    }

    public String getModel() {
        return model;
    }

    public String getTranscription() {
        return transcription;
    }

    public String getDescription() {
        return description;
    }

    public String getLatex() {
        return latex;
    }

    public String getCode() {
        return code;
    }

    public String getUncertainty() {
        return uncertainty;
    }

    public String getSearchText() {
        return searchText;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public String getContentKind() {
        return contentKind;
    }

    public String getImportance() {
        return importance;
    }

    public String getReadingOrder() {
        return readingOrder;
    }

    public String getLanguage() {
        return language;
    }

    public int getAttemptCount() {
        return attemptCount;
    }
}
