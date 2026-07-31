package com.noteflow.storage;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ManagedStorageFileServiceTest {
    private static final byte[] PNG = {
        (byte) 0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a
    };

    @TempDir
    Path temporaryDirectory;

    @Test
    void resolvesPngInsideManagedStorage() throws Exception {
        Path storage = temporaryDirectory.resolve("storage");
        Path uploadDir = Files.createDirectories(storage.resolve("uploads"));
        Path assetDir = Files.createDirectories(storage.resolve("rendered"));
        Path asset = Files.write(assetDir.resolve("page.png"), PNG);
        ManagedStorageFileService service = new ManagedStorageFileService(uploadDir.toString());

        assertEquals(asset.toRealPath(), service.resolvePngForRead(asset.toString()));
    }

    @Test
    void rejectsPngOutsideManagedStorage() throws Exception {
        Path uploadDir = Files.createDirectories(temporaryDirectory.resolve("storage/uploads"));
        Path outside = Files.write(temporaryDirectory.resolve("outside.png"), PNG);
        ManagedStorageFileService service = new ManagedStorageFileService(uploadDir.toString());

        assertThrows(IllegalArgumentException.class, () -> service.resolvePngForRead(outside.toString()));
    }

    @Test
    void rejectsNonPngContentInsideManagedStorage() throws Exception {
        Path storage = temporaryDirectory.resolve("storage");
        Path uploadDir = Files.createDirectories(storage.resolve("uploads"));
        Path assetDir = Files.createDirectories(storage.resolve("regions"));
        Path asset = Files.writeString(assetDir.resolve("region.png"), "not a png");
        ManagedStorageFileService service = new ManagedStorageFileService(uploadDir.toString());

        assertThrows(IllegalArgumentException.class, () -> service.resolvePngForRead(asset.toString()));
    }

    @Test
    void rejectsSymlinkThatEscapesManagedStorage() throws Exception {
        Path storage = temporaryDirectory.resolve("storage");
        Path uploadDir = Files.createDirectories(storage.resolve("uploads"));
        Path assetDir = Files.createDirectories(storage.resolve("rendered"));
        Path outside = Files.write(temporaryDirectory.resolve("outside.png"), PNG);
        Path link = Files.createSymbolicLink(assetDir.resolve("page.png"), outside);
        ManagedStorageFileService service = new ManagedStorageFileService(uploadDir.toString());

        assertThrows(IllegalArgumentException.class, () -> service.resolvePngForRead(link.toString()));
    }
}
