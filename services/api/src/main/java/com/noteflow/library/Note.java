package com.noteflow.library;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * A markdown note in the library. Unified model: a note may live in a folder
 * ({@code folderId}, {@code null} = Unfiled) and/or be derived from a source
 * document ({@code sourceDocumentId}). The per-document editable note is just a
 * note with {@code sourceDocumentId} set.
 */
@Entity
@Table(name = "notes")
public class Note {
    @Id
    private UUID id;

    private UUID userId;
    private UUID folderId;
    private String title;
    @Column(columnDefinition = "TEXT")
    private String markdown;
    private String sourceKind;
    private UUID sourceDocumentId;
    private Instant createdAt;
    private Instant updatedAt;

    protected Note() {
    }

    public Note(UUID id, UUID userId, UUID folderId, String title, String markdown, String sourceKind,
            UUID sourceDocumentId) {
        this.id = id;
        this.userId = userId;
        this.folderId = folderId;
        this.title = title;
        this.markdown = markdown == null ? "" : markdown;
        this.sourceKind = sourceKind;
        this.sourceDocumentId = sourceDocumentId;
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

    public void moveTo(UUID folderId) {
        this.folderId = folderId;
        this.updatedAt = Instant.now();
    }

    public void rename(String title) {
        this.title = title;
        this.updatedAt = Instant.now();
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public void setUpdatedAt(Instant updatedAt) {
        this.updatedAt = updatedAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getUserId() {
        return userId;
    }

    public UUID getFolderId() {
        return folderId;
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

    public UUID getSourceDocumentId() {
        return sourceDocumentId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
