package com.noteflow.editor;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_editable_notes", uniqueConstraints = {
    @UniqueConstraint(name = "uq_document_editable_notes_document", columnNames = "documentId")
})
public class DocumentEditableNote {
    @Id
    private UUID id;

    private UUID documentId;
    private UUID userId;
    private String title;
    @Column(columnDefinition = "TEXT")
    private String markdown;
    private String sourceKind;
    private Instant createdAt;
    private Instant updatedAt;

    protected DocumentEditableNote() {
    }

    public DocumentEditableNote(UUID id, UUID documentId, UUID userId, String title, String markdown,
            String sourceKind) {
        this.id = id;
        this.documentId = documentId;
        this.userId = userId;
        this.title = title;
        this.markdown = markdown == null ? "" : markdown;
        this.sourceKind = sourceKind;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public void update(String title, String markdown) {
        if (title != null && !title.isBlank()) {
            this.title = title;
        }
        this.markdown = markdown == null ? "" : markdown;
        this.updatedAt = Instant.now();
    }

    public void reset(String title, String markdown, String sourceKind) {
        this.title = title;
        this.markdown = markdown == null ? "" : markdown;
        this.sourceKind = sourceKind;
        this.updatedAt = Instant.now();
    }

    public UUID getId() {
        return id;
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public UUID getUserId() {
        return userId;
    }

    public String getTitle() {
        return title;
    }

    public String getMarkdown() {
        return markdown;
    }

    public String getSourceKind() {
        return sourceKind;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
