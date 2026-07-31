package com.noteflow.tasks;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class TaskOutboxScheduler {
    private final TaskOutboxPublisher publisher;
    private final int batchSize;
    private final int retentionDays;

    public TaskOutboxScheduler(
            TaskOutboxPublisher publisher,
            @Value("${noteflow.queue.outbox-batch-size:100}") int batchSize,
            @Value("${noteflow.queue.outbox-retention-days:7}") int retentionDays) {
        this.publisher = publisher;
        this.batchSize = Math.max(1, batchSize);
        this.retentionDays = Math.max(1, retentionDays);
    }

    @Scheduled(fixedDelayString = "${noteflow.queue.outbox-poll-millis:250}")
    public void publishPendingEvents() {
        publisher.publishBatch(batchSize);
    }

    @Scheduled(cron = "${noteflow.queue.outbox-cleanup-cron:0 41 3 * * *}")
    public void purgePublishedEvents() {
        publisher.purgePublishedEvents(retentionDays);
    }
}
