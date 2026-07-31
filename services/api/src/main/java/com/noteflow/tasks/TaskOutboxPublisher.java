package com.noteflow.tasks;

import com.noteflow.queue.DocumentTaskQueue;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TaskOutboxPublisher {
    private final TaskOutboxRepository outbox;
    private final TaskRepository tasks;
    private final DocumentTaskQueue queue;

    public TaskOutboxPublisher(TaskOutboxRepository outbox, TaskRepository tasks, DocumentTaskQueue queue) {
        this.outbox = outbox;
        this.tasks = tasks;
        this.queue = queue;
    }

    @Transactional
    public int publishBatch(int limit) {
        var events = outbox.lockNextBatch(Math.max(1, limit));
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
                event.markPublished();
            } catch (RuntimeException error) {
                event.markFailed(error);
            }
        }
        return events.size();
    }

    @Transactional
    public int purgePublishedEvents(int retentionDays) {
        int days = Math.max(1, retentionDays);
        return outbox.deletePublishedBefore(Instant.now().minus(days, ChronoUnit.DAYS));
    }
}
