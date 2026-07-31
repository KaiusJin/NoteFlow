package com.noteflow.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.noteflow.queue.DocumentTaskQueue;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

class TaskOutboxPublisherTest {
    private TaskOutboxRepository outbox;
    private TaskRepository tasks;
    private DocumentTaskQueue queue;
    private TaskOutboxPublisher publisher;

    @BeforeEach
    void setup() {
        outbox = Mockito.mock(TaskOutboxRepository.class);
        tasks = Mockito.mock(TaskRepository.class);
        queue = Mockito.mock(DocumentTaskQueue.class);
        publisher = new TaskOutboxPublisher(outbox, tasks, queue);
    }

    @Test
    void marksEventPublishedAfterRedisAcceptsIt() {
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.PARSE_DOCUMENT);
        TaskOutbox event = new TaskOutbox(UUID.randomUUID(), task.getId(), null, null, null);
        when(outbox.lockNextBatch(10)).thenReturn(List.of(event));
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));

        assertEquals(1, publisher.publishBatch(10));

        verify(queue).enqueue(task, null, null, null, event.getId());
        assertNotNull(event.getPublishedAt());
        assertEquals(0, event.getRetryCount());
        assertNull(event.getLastError());
    }

    @Test
    void retainsEventWithBackoffWhenRedisFails() {
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.PARSE_DOCUMENT);
        TaskOutbox event = new TaskOutbox(UUID.randomUUID(), task.getId(), null, null, null);
        when(outbox.lockNextBatch(10)).thenReturn(List.of(event));
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));
        doThrow(new IllegalStateException("redis unavailable"))
            .when(queue).enqueue(task, null, null, null, event.getId());

        assertEquals(1, publisher.publishBatch(10));

        assertNull(event.getPublishedAt());
        assertEquals(1, event.getRetryCount());
        assertEquals("redis unavailable", event.getLastError());
    }
}
