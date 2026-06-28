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
        String payload = """
            {"taskId":"%s","documentId":"%s","userId":"%s","taskType":"%s","priority":%d,"enqueuedAt":%f}
            """.formatted(
                task.getId(),
                task.getDocumentId(),
                task.getUserId(),
                task.getTaskType(),
                task.getPriority(),
                Instant.now().toEpochMilli() / 1000.0
            ).trim();
        redis.opsForList().rightPush(priorityQueueName(task.getPriority()), payload);
    }

    private String priorityQueueName(int priority) {
        return queueName + ":priority:" + priority;
    }
}
