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
    String aiNoteStatus,
    String embeddingStatus,
    Instant createdAt
) {
    public static DocumentResponse from(Document document, String aiNoteStatus, String embeddingStatus) {
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
            aiNoteStatus,
            embeddingStatus,
            document.getCreatedAt()
        );
    }
}
