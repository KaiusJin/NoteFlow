package com.noteflow.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.noteflow.queue.DocumentTaskQueue;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.core.ListOperations;
import org.springframework.data.redis.core.StringRedisTemplate;

class TaskPriorityTest {
    @Test
    void mapsInteractiveVisibleAndBackgroundPriorities() {
        assertEquals(0, Task.priorityFor(TaskType.ASK_DOCUMENT));
        assertEquals(0, Task.priorityFor(TaskType.EXPORT_MARKDOWN));
        assertEquals(1, Task.priorityFor(TaskType.PARSE_DOCUMENT));
        assertEquals(1, Task.priorityFor(TaskType.GENERATE_NOTES));
        assertEquals(2, Task.priorityFor(TaskType.GENERATE_EMBEDDINGS));
    }

    @Test
    void newTaskPersistsItsDerivedPriority() {
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.GENERATE_EMBEDDINGS);
        assertEquals(2, task.getPriority());
    }

    @Test
    void enqueueUsesPhysicalPriorityQueueAndIncludesPriorityMetadata() {
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        @SuppressWarnings("unchecked")
        ListOperations<String, String> lists = mock(ListOperations.class);
        when(redis.opsForList()).thenReturn(lists);
        DocumentTaskQueue queue = new DocumentTaskQueue("queue:test", redis);
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.GENERATE_EMBEDDINGS);

        queue.enqueue(task);

        ArgumentCaptor<String> payload = ArgumentCaptor.forClass(String.class);
        verify(lists).rightPush(eq("queue:test:priority:2"), payload.capture());
        assertTrue(payload.getValue().contains("\"priority\":2"));
        assertTrue(payload.getValue().contains("\"enqueuedAt\":"));
    }
}
