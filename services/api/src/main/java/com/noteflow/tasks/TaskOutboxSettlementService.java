package com.noteflow.tasks;

import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TaskOutboxSettlementService {
    private final TaskOutboxRepository outbox;
    private final TaskRepository tasks;

    public TaskOutboxSettlementService(TaskOutboxRepository outbox, TaskRepository tasks) {
        this.outbox = outbox;
        this.tasks = tasks;
    }

    @Transactional
    public boolean markPublished(UUID eventId, UUID claimToken) {
        return outbox.findByIdAndClaimToken(eventId, claimToken)
            .map(event -> {
                event.markPublished();
                return true;
            })
            .orElse(false);
    }

    @Transactional
    public FailureResult markFailed(UUID eventId, UUID claimToken, RuntimeException error, int maxAttempts) {
        return outbox.findByIdAndClaimToken(eventId, claimToken)
            .map(event -> {
                event.markFailed(error);
                boolean deadLettered = event.getRetryCount() >= Math.max(1, maxAttempts);
                if (deadLettered) {
                    event.markDeadLetter();
                    tasks.findById(event.getTaskId()).ifPresent(task -> task.failDispatch(
                        "Task could not be delivered to Redis after " + event.getRetryCount() + " attempts"
                    ));
                }
                return new FailureResult(true, deadLettered, event.getRetryCount(), event.getLastError());
            })
            .orElseGet(() -> new FailureResult(false, false, 0, "Outbox claim expired"));
    }

    public record FailureResult(boolean settled, boolean deadLettered, int retryCount, String lastError) {}
}
