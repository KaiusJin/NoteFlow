package com.noteflow.tasks;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "task_outbox")
public class TaskOutbox {
    @Id
    private UUID id;

    @Column(name = "task_id", nullable = false, unique = true)
    private UUID taskId;

    @Column(name = "attempt_id")
    private UUID attemptId;

    @Column(name = "conversation_id")
    private UUID conversationId;

    @Column(name = "message_id")
    private UUID messageId;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "available_at", nullable = false)
    private Instant availableAt;

    @Column(name = "published_at")
    private Instant publishedAt;

    @Column(name = "retry_count", nullable = false)
    private int retryCount;

    @Column(name = "last_error", columnDefinition = "TEXT")
    private String lastError;

    /** Terminal state: set once retry attempts exceed the configured maximum. */
    @Column(name = "dead_letter_at")
    private Instant deadLetterAt;

    @Column(name = "claim_token")
    private UUID claimToken;

    @Column(name = "claimed_at")
    private Instant claimedAt;

    protected TaskOutbox() {
    }

    public TaskOutbox(UUID id, UUID taskId, UUID attemptId, UUID conversationId, UUID messageId) {
        this.id = id;
        this.taskId = taskId;
        this.attemptId = attemptId;
        this.conversationId = conversationId;
        this.messageId = messageId;
        this.createdAt = Instant.now();
        this.availableAt = createdAt;
    }

    public UUID getId() {
        return id;
    }

    public UUID getTaskId() {
        return taskId;
    }

    public UUID getAttemptId() {
        return attemptId;
    }

    public UUID getConversationId() {
        return conversationId;
    }

    public UUID getMessageId() {
        return messageId;
    }

    public Instant getPublishedAt() {
        return publishedAt;
    }

    public int getRetryCount() {
        return retryCount;
    }

    public String getLastError() {
        return lastError;
    }

    public Instant getDeadLetterAt() {
        return deadLetterAt;
    }

    public UUID getClaimToken() {
        return claimToken;
    }

    public Instant getClaimedAt() {
        return claimedAt;
    }

    /** Stops further delivery attempts for this event (terminal FAILED state). */
    public void markDeadLetter() {
        deadLetterAt = Instant.now();
        releaseClaim();
    }

    public void markPublished() {
        publishedAt = Instant.now();
        lastError = null;
        releaseClaim();
    }

    public void markFailed(RuntimeException error) {
        retryCount += 1;
        long backoffSeconds = Math.min(300, 1L << Math.min(retryCount - 1, 8));
        availableAt = Instant.now().plusSeconds(backoffSeconds);
        String message = error.getMessage();
        lastError = (message == null ? error.getClass().getSimpleName() : message);
        if (lastError.length() > 2_000) {
            lastError = lastError.substring(0, 2_000);
        }
        releaseClaim();
    }

    private void releaseClaim() {
        claimToken = null;
        claimedAt = null;
    }
}
