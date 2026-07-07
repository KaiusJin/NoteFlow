package com.noteflow.queue;

import com.noteflow.tasks.Task;
import java.time.Instant;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

@Service
public class DocumentTaskQueue {
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
        String attemptField = attemptId == null ? "" : ",\"attemptId\":\"" + attemptId + "\"";
        String conversationField = conversationId == null ? "" : ",\"conversationId\":\"" + conversationId + "\"";
        String messageField = messageId == null ? "" : ",\"messageId\":\"" + messageId + "\"";
        String payload = """
            {"taskId":"%s","documentId":%s,"userId":"%s","taskType":"%s","priority":%d,"enqueuedAt":%f%s%s%s}
            """.formatted(
                task.getId(),
                task.getDocumentId() == null ? "null" : "\"" + task.getDocumentId() + "\"",
                task.getUserId(),
                task.getTaskType(),
                task.getPriority(),
                Instant.now().toEpochMilli() / 1000.0,
                attemptField,
                conversationField,
                messageField
            ).trim();
        redis.opsForList().rightPush(priorityQueueName(task.getPriority()), payload);
    }

    private String priorityQueueName(int priority) {
        return queueName + ":priority:" + priority;
    }
}
