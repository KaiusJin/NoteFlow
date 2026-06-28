package com.noteflow.parsing;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "document_parse_manifests")
public class DocumentParseManifest {
    @Id
    private UUID documentId;

    @Column(columnDefinition = "TEXT", nullable = false)
    private String manifestJson;

    private Instant updatedAt;

    protected DocumentParseManifest() {
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public String getManifestJson() {
        return manifestJson;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
