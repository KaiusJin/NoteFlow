package com.noteflow.storage;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Locale;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;

@Service
@Profile("!cloud")
public class ManagedStorageFileService implements PngObjectStorage {
    private static final byte[] PNG_SIGNATURE = {
        (byte) 0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a
    };

    private final Path storageRoot;

    public ManagedStorageFileService(@Value("${noteflow.storage.upload-dir}") String uploadDir) {
        Path normalizedUploadDir = Path.of(uploadDir).toAbsolutePath().normalize();
        this.storageRoot = normalizedUploadDir.getParent();
        if (storageRoot == null) {
            throw new IllegalArgumentException("Upload directory must have a managed storage root");
        }
    }

    public Path resolvePngForRead(String storedPath) {
        if (storedPath == null || storedPath.isBlank()) {
            throw new IllegalArgumentException("Asset path is missing");
        }

        try {
            Path realRoot = storageRoot.toRealPath();
            Path realFile = Path.of(storedPath).toAbsolutePath().normalize().toRealPath();
            if (!realFile.startsWith(realRoot) || !Files.isRegularFile(realFile)) {
                throw new IllegalArgumentException("Asset path is outside managed storage");
            }
            if (!realFile.getFileName().toString().toLowerCase(Locale.ROOT).endsWith(".png")
                    || !hasPngSignature(realFile)) {
                throw new IllegalArgumentException("Asset is not a valid PNG file");
            }
            return realFile;
        } catch (IOException error) {
            throw new IllegalArgumentException("Asset file not found", error);
        }
    }

    @Override
    public StoredObject readPng(String storedPath) {
        Path path = resolvePngForRead(storedPath);
        try {
            return new StoredObject(Files.readAllBytes(path), "image/png");
        } catch (IOException error) {
            throw new IllegalArgumentException("Asset file could not be read", error);
        }
    }

    private boolean hasPngSignature(Path file) throws IOException {
        try (InputStream input = Files.newInputStream(file)) {
            byte[] signature = input.readNBytes(PNG_SIGNATURE.length);
            return Arrays.equals(signature, PNG_SIGNATURE);
        }
    }
}
