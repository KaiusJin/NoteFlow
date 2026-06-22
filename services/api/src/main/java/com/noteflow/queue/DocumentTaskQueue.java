package com.noteflow.queue;

import com.noteflow.tasks.Task;
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
            {"taskId":"%s","documentId":"%s","userId":"%s","taskType":"%s"}
            """.formatted(task.getId(), task.getDocumentId(), task.getUserId(), task.getTaskType()).trim();
        redis.opsForList().rightPush(queueName, payload);
    }
}
