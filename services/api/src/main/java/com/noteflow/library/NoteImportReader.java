package com.noteflow.library;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

@Component
public class NoteImportReader {
    private static final int MAX_CONFIGURED_BYTES = 50 * 1024 * 1024;
    private static final Set<String> ALLOWED_EXTENSIONS = Set.of(".md", ".markdown", ".txt");
    private static final Set<String> ALLOWED_CONTENT_TYPES = Set.of(
        "text/markdown",
        "text/x-markdown",
        "text/plain",
        "application/octet-stream"
    );

    private final int maxBytes;

    public NoteImportReader(@Value("${noteflow.storage.note-import-max-bytes}") int maxBytes) {
        if (maxBytes < 1 || maxBytes > MAX_CONFIGURED_BYTES) {
            throw new IllegalArgumentException("Note import size limit must be between 1 byte and 50 MiB");
        }
        this.maxBytes = maxBytes;
    }

    public String readUtf8(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("Note import file is required");
        }
        validateFilename(file.getOriginalFilename());
        validateContentType(file.getContentType());
        if (file.getSize() > maxBytes) {
            throw new IllegalArgumentException("Note import exceeds the configured size limit");
        }

        try {
            byte[] body = file.getInputStream().readNBytes(maxBytes + 1);
            if (body.length > maxBytes) {
                throw new IllegalArgumentException("Note import exceeds the configured size limit");
            }
            return decodeUtf8(body);
        } catch (IOException error) {
            throw new IllegalArgumentException("Could not read uploaded note", error);
        }
    }

    private void validateFilename(String filename) {
        String normalized = filename == null ? "" : filename.trim().toLowerCase(Locale.ROOT);
        boolean allowed = ALLOWED_EXTENSIONS.stream().anyMatch(normalized::endsWith);
        if (!allowed) {
            throw new IllegalArgumentException("Note import must be a .md, .markdown, or .txt file");
        }
    }

    private void validateContentType(String contentType) {
        if (contentType == null || contentType.isBlank()) {
            return;
        }
        String normalized = contentType.split(";", 2)[0].trim().toLowerCase(Locale.ROOT);
        if (!ALLOWED_CONTENT_TYPES.contains(normalized)) {
            throw new IllegalArgumentException("Note import content type is not supported");
        }
    }

    private String decodeUtf8(byte[] body) {
        try {
            return StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(body))
                .toString();
        } catch (CharacterCodingException error) {
            throw new IllegalArgumentException("Note import must contain valid UTF-8 text", error);
        }
    }
}
