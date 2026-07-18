package com.noteflow.library;

import java.time.Instant;
import java.util.UUID;

public record FolderResponse(
    UUID id,
    UUID parentId,
    String name,
    Instant createdAt,
    Instant updatedAt
) {
    public static FolderResponse from(Folder folder) {
        return new FolderResponse(
            folder.getId(),
            folder.getParentId(),
            folder.getName(),
            folder.getCreatedAt(),
            folder.getUpdatedAt()
        );
    }
}
