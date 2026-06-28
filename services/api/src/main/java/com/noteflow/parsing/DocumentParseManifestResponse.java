package com.noteflow.parsing;

import java.time.Instant;
import java.util.UUID;

public record DocumentParseManifestResponse(
    UUID documentId,
    String manifestJson,
    Instant updatedAt
) {
    public static DocumentParseManifestResponse from(DocumentParseManifest manifest) {
        return new DocumentParseManifestResponse(
            manifest.getDocumentId(),
            manifest.getManifestJson(),
            manifest.getUpdatedAt()
        );
    }
}
