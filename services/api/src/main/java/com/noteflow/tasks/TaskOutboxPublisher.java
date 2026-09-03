package com.noteflow.tasks;

import com.noteflow.queue.DocumentTaskQueue;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TaskOutboxPublisher {
    private static final Logger log = LoggerFactory.getLogger(TaskOutboxPublisher.class);

    private final TaskOutboxRepository outbox;
    private final TaskOutboxClaimService claims;
    private final TaskOutboxSettlementService settlements;
    private final TaskRepository tasks;
    private final DocumentTaskQueue queue;
    private final WorkerWakeupCoordinator workerWakeup;
    private final int maxAttempts;
    private final int claimTimeoutSeconds;

    public TaskOutboxPublisher(
            TaskOutboxRepository outbox,
            TaskOutboxClaimService claims,
            TaskOutboxSettlementService settlements,
            TaskRepository tasks,
            DocumentTaskQueue queue,
            WorkerWakeupCoordinator workerWakeup,
            @Value("${noteflow.queue.outbox-max-attempts:12}") int maxAttempts,
            @Value("${noteflow.queue.outbox-claim-timeout-seconds:30}") int claimTimeoutSeconds) {
        this.outbox = outbox;
        this.claims = claims;
        this.settlements = settlements;
        this.tasks = tasks;
        this.queue = queue;
        this.workerWakeup = workerWakeup;
        this.maxAttempts = Math.max(1, maxAttempts);
        this.claimTimeoutSeconds = Math.max(5, claimTimeoutSeconds);
    }

    public int publishBatch(int limit) {
        UUID claimToken = UUID.randomUUID();
        var events = claims.claimBatch(claimToken, Math.max(1, limit), claimTimeoutSeconds);
        int published = 0;
        for (TaskOutbox event : events) {
            try {
                Task task = tasks.findById(event.getTaskId())
                    .orElseThrow(() -> new IllegalStateException("Outbox task no longer exists"));
                queue.enqueue(
                    task,
                    event.getAttemptId(),
                    event.getConversationId(),
                    event.getMessageId(),
                    event.getId()
                );
                if (!settlements.markPublished(event.getId(), claimToken)) {
                    log.warn("Outbox claim expired after Redis publish event={} task={}", event.getId(), event.getTaskId());
                } else {
                    published++;
                }
            } catch (RuntimeException error) {
                var result = settlements.markFailed(event.getId(), claimToken, error, maxAttempts);
                if (result.deadLettered()) {
                    log.warn("Outbox event {} dead-lettered for task {} after {} failed attempts: {}",
                        event.getId(), event.getTaskId(), result.retryCount(), result.lastError());
                }
            }
        }
        if (published > 0) {
            workerWakeup.requestWakeup();
        }
        return events.size();
    }

    @Transactional
    public int purgePublishedEvents(int retentionDays) {
        int days = Math.max(1, retentionDays);
        return outbox.deletePublishedBefore(Instant.now().minus(days, ChronoUnit.DAYS));
    }
}
