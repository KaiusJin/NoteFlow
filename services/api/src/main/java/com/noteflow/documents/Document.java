package com.noteflow.documents;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "documents")
public class Document {
    @Id
    private UUID id;

    private UUID userId;
    private String title;
    private String originalFilename;
    private String fileType;
    private long fileSize;
    private String storagePath;
    private Integer pageCount;
    private String language;

    @Enumerated(EnumType.STRING)
    private DocumentType documentType;

    @Enumerated(EnumType.STRING)
    private ContentSourceType contentSourceType;

    @Enumerated(EnumType.STRING)
    private DocumentStatus status;

    private Instant createdAt;
    private Instant updatedAt;

    protected Document() {
    }

    public Document(UUID id, UUID userId, String title, String originalFilename, String fileType, long fileSize,
            String storagePath, DocumentType documentType) {
        this.id = id;
        this.userId = userId;
        this.title = title;
        this.originalFilename = originalFilename;
        this.fileType = fileType;
        this.fileSize = fileSize;
        this.storagePath = storagePath;
        this.documentType = documentType;
        this.contentSourceType = ContentSourceType.UNKNOWN;
        this.status = DocumentStatus.UPLOADED;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getUserId() {
        return userId;
    }

    public String getTitle() {
        return title;
    }

    public String getOriginalFilename() {
        return originalFilename;
    }

    public String getFileType() {
        return fileType;
    }

    public long getFileSize() {
        return fileSize;
    }

    public String getStoragePath() {
        return storagePath;
    }

    public Integer getPageCount() {
        return pageCount;
    }

    public DocumentType getDocumentType() {
        return documentType;
    }

    public ContentSourceType getContentSourceType() {
        return contentSourceType;
    }

    public DocumentStatus getStatus() {
        return status;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
