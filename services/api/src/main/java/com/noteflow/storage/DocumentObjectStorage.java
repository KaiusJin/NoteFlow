package com.noteflow.storage;

import java.util.UUID;
import org.springframework.web.multipart.MultipartFile;

public interface DocumentObjectStorage {
    StoredFile savePdf(UUID userId, UUID documentId, MultipartFile file);

    void deleteIfExists(String storagePath);
}
