package com.noteflow.common;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;

public record OpaqueCursor(Instant timestamp, UUID id) {
    public static OpaqueCursor decode(String value) {
        if (value == null || value.isBlank()) return null;
        try {
            String decoded = new String(
                Base64.getUrlDecoder().decode(value),
                StandardCharsets.UTF_8
            );
            String[] parts = decoded.split("\\|", 2);
            return new OpaqueCursor(Instant.parse(parts[0]), UUID.fromString(parts[1]));
        } catch (RuntimeException error) {
            throw new IllegalArgumentException("Invalid cursor", error);
        }
    }

    public String encode() {
        String raw = timestamp + "|" + id;
        return Base64.getUrlEncoder().withoutPadding()
            .encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }
}
