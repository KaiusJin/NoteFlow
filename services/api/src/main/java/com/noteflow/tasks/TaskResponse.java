package com.noteflow.tasks;

import java.time.Instant;
import java.util.UUID;

public record TaskResponse(
    UUID id,
    UUID documentId,
    TaskType taskType,
    TaskStatus status,
    TaskStep currentStep,
    int progress,
    String errorMessage,
    int retryCount,
    int priority,
    Instant createdAt
) {
    public static TaskResponse from(Task task) {
        return new TaskResponse(
            task.getId(),
            task.getDocumentId(),
            task.getTaskType(),
            task.getStatus(),
            task.getCurrentStep(),
            task.getProgress(),
            task.getErrorMessage(),
            task.getRetryCount(),
            task.getPriority(),
            task.getCreatedAt()
        );
    }
}
