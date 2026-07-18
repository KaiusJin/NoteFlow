package com.noteflow.library;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * A user-created folder in the notes library. Folders form a tree via
 * {@code parentId} ({@code null} = top level).
 */
@Entity
@Table(name = "folders")
public class Folder {
    @Id
    private UUID id;

    private UUID userId;
    private UUID parentId;
    private String name;
    private Instant createdAt;
    private Instant updatedAt;

    protected Folder() {
    }

    public Folder(UUID id, UUID userId, UUID parentId, String name) {
        this.id = id;
        this.userId = userId;
        this.parentId = parentId;
        this.name = name;
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public void rename(String name) {
        this.name = name;
        this.updatedAt = Instant.now();
    }

    public void moveTo(UUID parentId) {
        this.parentId = parentId;
        this.updatedAt = Instant.now();
    }

    public UUID getId() {
        return id;
    }

    public UUID getUserId() {
        return userId;
    }

    public UUID getParentId() {
        return parentId;
    }

    public String getName() {
        return name;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }
}
