package com.noteflow.queue;

import com.noteflow.tasks.Task;
import java.time.Instant;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

@Service
public class DocumentTaskQueue {
    private static final DefaultRedisScript<Long> PENDING_TASK_COUNT = new DefaultRedisScript<>(
        "local total = 0; for _, key in ipairs(KEYS) do total = total + redis.call('LLEN', key); end; return total",
        Long.class
    );
    private final String queueName;
    private final StringRedisTemplate redis;

    public DocumentTaskQueue(@Value("${noteflow.queue.document-analysis}") String queueName, StringRedisTemplate redis) {
        this.queueName = queueName;
        this.redis = redis;
    }

    public void enqueue(Task task) {
        enqueue(task, null);
    }

    public void enqueue(Task task, java.util.UUID attemptId) {
        enqueue(task, attemptId, null, null);
    }

    public void enqueue(Task task, java.util.UUID attemptId, java.util.UUID conversationId, java.util.UUID messageId) {
        enqueue(task, attemptId, conversationId, messageId, null);
    }

    public void enqueue(Task task, java.util.UUID attemptId, java.util.UUID conversationId, java.util.UUID messageId,
            java.util.UUID eventId) {
        String attemptField = attemptId == null ? "" : ",\"attemptId\":\"" + attemptId + "\"";
        String conversationField = conversationId == null ? "" : ",\"conversationId\":\"" + conversationId + "\"";
        String messageField = messageId == null ? "" : ",\"messageId\":\"" + messageId + "\"";
        String eventField = eventId == null ? "" : ",\"eventId\":\"" + eventId + "\"";
        String payload = """
            {"taskId":"%s","documentId":%s,"userId":"%s","taskType":"%s","priority":%d,"enqueuedAt":%d%s%s%s%s}
            """.formatted(
                task.getId(),
                task.getDocumentId() == null ? "null" : "\"" + task.getDocumentId() + "\"",
                task.getUserId(),
                task.getTaskType(),
                task.getPriority(),
                Instant.now().toEpochMilli(),
                attemptField,
                conversationField,
                messageField,
                eventField
            ).trim();
        redis.opsForList().rightPush(priorityQueueName(task.getPriority()), payload);
    }

    public boolean hasPendingTasks() {
        Long count = redis.execute(
            PENDING_TASK_COUNT,
            List.of(priorityQueueName(0), priorityQueueName(1), priorityQueueName(2))
        );
        return count != null && count > 0;
    }

    private String priorityQueueName(int priority) {
        return queueName + ":priority:" + priority;
    }
}
