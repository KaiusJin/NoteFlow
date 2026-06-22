package com.noteflow.markdown;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_markdown_pages", uniqueConstraints = {
    @UniqueConstraint(name = "uq_markdown_pages_document_page", columnNames = {"documentId", "pageNumber"})
})
public class DocumentMarkdownPage {
    @Id
    private UUID id;

    private UUID documentId;
    private int pageNumber;
    @Column(columnDefinition = "TEXT")
    private String markdown;
    private String sourceType;
    private double qualityScore;
    @Column(columnDefinition = "TEXT")
    private String warningsJson;
    @Column(columnDefinition = "TEXT")
    private String structureJson;
    private Instant createdAt;
    private Instant updatedAt;

    protected DocumentMarkdownPage() {
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

    public String getMarkdown() {
        return markdown;
    }

    public String getSourceType() {
        return sourceType;
    }

    public double getQualityScore() {
        return qualityScore;
    }

    public String getWarningsJson() {
        return warningsJson;
    }

    public String getStructureJson() {
        return structureJson;
    }
}
