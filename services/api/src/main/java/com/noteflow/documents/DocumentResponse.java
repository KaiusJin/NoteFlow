package com.noteflow.documents;

import java.time.Instant;
import java.util.UUID;

public record DocumentResponse(
    UUID id,
    String title,
    String originalFilename,
    String fileType,
    long fileSize,
    Integer pageCount,
    DocumentType documentType,
    ContentSourceType contentSourceType,
    DocumentStatus status,
    Instant createdAt
) {
    public static DocumentResponse from(Document document) {
        return new DocumentResponse(
            document.getId(),
            document.getTitle(),
            document.getOriginalFilename(),
            document.getFileType(),
            document.getFileSize(),
            document.getPageCount(),
            document.getDocumentType(),
            document.getContentSourceType(),
            document.getStatus(),
            document.getCreatedAt()
        );
    }
}
