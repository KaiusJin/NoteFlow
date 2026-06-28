package com.noteflow.tasks;

import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Column;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "tasks")
public class Task {
    @Id
    private UUID id;

    private UUID documentId;
    private UUID userId;

    @Enumerated(EnumType.STRING)
    private TaskType taskType;

    @Enumerated(EnumType.STRING)
    private TaskStatus status;

    @Enumerated(EnumType.STRING)
    private TaskStep currentStep;

    private int progress;

    @Column(columnDefinition = "TEXT")
    private String errorMessage;
    private int retryCount;
    private Integer priority;
    private Instant createdAt;
    private Instant startedAt;
    private Instant completedAt;
    private Instant updatedAt;

    protected Task() {
    }

    public Task(UUID id, UUID documentId, UUID userId, TaskType taskType) {
        this.id = id;
        this.documentId = documentId;
        this.userId = userId;
        this.taskType = taskType;
        this.status = TaskStatus.PENDING;
        this.currentStep = TaskStep.UPLOADED;
        this.progress = 0;
        this.retryCount = 0;
        this.priority = priorityFor(taskType);
        this.createdAt = Instant.now();
        this.updatedAt = this.createdAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getDocumentId() {
        return documentId;
    }

    public UUID getUserId() {
        return userId;
    }

    public TaskType getTaskType() {
        return taskType;
    }

    public TaskStatus getStatus() {
        return status;
    }

    public TaskStep getCurrentStep() {
        return currentStep;
    }

    public int getProgress() {
        return progress;
    }

    public String getErrorMessage() {
        return errorMessage;
    }

    public int getRetryCount() {
        return retryCount;
    }

    public int getPriority() {
        return priority == null ? priorityFor(taskType) : priority;
    }

    public static int priorityFor(TaskType taskType) {
        return switch (taskType) {
            case ASK_DOCUMENT, EXPORT_MARKDOWN -> 0;
            case GENERATE_EMBEDDINGS -> 2;
            default -> 1;
        };
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
