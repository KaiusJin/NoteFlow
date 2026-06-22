package com.noteflow.markdown;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_markdown_documents", uniqueConstraints = {
    @UniqueConstraint(name = "uq_markdown_documents_document", columnNames = "documentId")
})
public class DocumentMarkdownDocument {
    @Id
    private UUID id;

    private UUID documentId;
    @Column(columnDefinition = "TEXT")
    private String markdown;
    @Column(columnDefinition = "TEXT")
    private String structureJson;
    @Column(columnDefinition = "TEXT")
    private String qualityReportJson;
    private Instant createdAt;
    private Instant updatedAt;

    protected DocumentMarkdownDocument() {
    }

    public UUID getId() {
        return id;
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public String getMarkdown() {
        return markdown;
    }

    public String getStructureJson() {
        return structureJson;
    }

    public String getQualityReportJson() {
        return qualityReportJson;
    }
}
