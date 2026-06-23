package com.noteflow.notes;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_ai_notes", uniqueConstraints = {
    @UniqueConstraint(name = "uq_document_ai_notes_document_version", columnNames = {"documentId", "noteVersion"})
})
public class DocumentAiNote {
    @Id
    private UUID id;

    private UUID documentId;
    private int noteVersion;
    private String status;
    private String title;
    @Column(columnDefinition = "TEXT")
    private String markdown;
    @Column(columnDefinition = "TEXT")
    private String summary;
    private String modelProvider;
    private String modelName;
    private String promptVersion;
    private String sourceDocumentVersion;
    @Column(columnDefinition = "TEXT")
    private String qualityReportJson;
    @Column(columnDefinition = "TEXT")
    private String metadataJson;
    private Instant createdAt;
    private Instant updatedAt;

    protected DocumentAiNote() {
    }

    public DocumentAiNote(UUID id, UUID documentId, int noteVersion, String title) {
        this.id = id;
        this.documentId = documentId;
        this.noteVersion = noteVersion;
        this.status = "GENERATING";
        this.title = title;
        this.markdown = "";
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public int getNoteVersion() {
        return noteVersion;
    }

    public String getStatus() {
        return status;
    }

    public String getTitle() {
        return title;
    }

    public String getMarkdown() {
        return markdown;
    }

    public String getSummary() {
        return summary;
    }

    public String getModelProvider() {
        return modelProvider;
    }

    public String getModelName() {
        return modelName;
    }

    public String getPromptVersion() {
        return promptVersion;
    }

    public String getSourceDocumentVersion() {
        return sourceDocumentVersion;
    }

    public String getQualityReportJson() {
        return qualityReportJson;
    }

    public String getMetadataJson() {
        return metadataJson;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
