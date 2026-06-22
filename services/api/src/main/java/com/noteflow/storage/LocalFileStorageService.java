package com.noteflow.storage;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

@Service
public class LocalFileStorageService {
    private final Path uploadDir;

    public LocalFileStorageService(@Value("${noteflow.storage.upload-dir}") String uploadDir) {
        this.uploadDir = Path.of(uploadDir).toAbsolutePath().normalize();
    }

    public StoredFile savePdf(UUID documentId, MultipartFile file) {
        try {
            Files.createDirectories(uploadDir);
            Path target = uploadDir.resolve(documentId + ".pdf");
            file.transferTo(target);
            return new StoredFile(target.toString(), file.getContentType(), file.getSize());
        } catch (IOException ex) {
            throw new IllegalStateException("Failed to store uploaded PDF", ex);
        }
    }
}
