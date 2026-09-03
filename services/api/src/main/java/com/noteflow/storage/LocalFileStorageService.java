package com.noteflow.storage;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

@Service
@Profile("!cloud")
public class LocalFileStorageService implements DocumentObjectStorage {
    private final Path uploadDir;

    public LocalFileStorageService(@Value("${noteflow.storage.upload-dir}") String uploadDir) {
        this.uploadDir = Path.of(uploadDir).toAbsolutePath().normalize();
    }

    @Override
    public StoredFile savePdf(UUID userId, UUID documentId, MultipartFile file) {
        try {
            Files.createDirectories(uploadDir);
            Path target = uploadDir.resolve(documentId + ".pdf");
            file.transferTo(target);
            return new StoredFile(target.toString(), file.getContentType(), file.getSize());
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to store uploaded PDF", ex);
        }
    }

    @Override
    public void deleteIfExists(String storagePath) {
        if (storagePath == null || storagePath.isBlank()) {
            return;
        }
        try {
            Path target = Path.of(storagePath).toAbsolutePath().normalize();
            if (!target.startsWith(uploadDir)) {
                throw new IllegalArgumentException("Refusing to delete a file outside managed upload storage");
            }
            Files.deleteIfExists(target);
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to clean up uploaded PDF", ex);
        }
    }
}
