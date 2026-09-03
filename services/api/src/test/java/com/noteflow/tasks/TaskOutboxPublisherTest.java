package com.noteflow.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.never;
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
    private TaskOutboxClaimService claims;
    private TaskOutboxSettlementService settlements;
    private DocumentTaskQueue queue;
    private WorkerWakeupCoordinator workerWakeup;
    private TaskOutboxPublisher publisher;

    @BeforeEach
    void setup() {
        outbox = Mockito.mock(TaskOutboxRepository.class);
        tasks = Mockito.mock(TaskRepository.class);
        claims = Mockito.mock(TaskOutboxClaimService.class);
        settlements = Mockito.mock(TaskOutboxSettlementService.class);
        queue = Mockito.mock(DocumentTaskQueue.class);
        workerWakeup = Mockito.mock(WorkerWakeupCoordinator.class);
        publisher = new TaskOutboxPublisher(outbox, claims, settlements, tasks, queue, workerWakeup, 12, 30);
    }

    @Test
    void marksEventPublishedAfterRedisAcceptsIt() {
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.PARSE_DOCUMENT);
        TaskOutbox event = new TaskOutbox(UUID.randomUUID(), task.getId(), null, null, null);
        when(claims.claimBatch(any(UUID.class), eq(10), eq(30))).thenReturn(List.of(event));
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));
        when(settlements.markPublished(eq(event.getId()), any(UUID.class))).thenReturn(true);

        assertEquals(1, publisher.publishBatch(10));

        verify(queue).enqueue(task, null, null, null, event.getId());
        verify(settlements).markPublished(eq(event.getId()), any(UUID.class));
        verify(workerWakeup).requestWakeup();
    }

    @Test
    void retainsEventWithBackoffWhenRedisFails() {
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.PARSE_DOCUMENT);
        TaskOutbox event = new TaskOutbox(UUID.randomUUID(), task.getId(), null, null, null);
        when(claims.claimBatch(any(UUID.class), eq(10), eq(30))).thenReturn(List.of(event));
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));
        doThrow(new IllegalStateException("redis unavailable"))
            .when(queue).enqueue(task, null, null, null, event.getId());
        when(settlements.markFailed(eq(event.getId()), any(UUID.class), any(IllegalStateException.class), eq(12)))
            .thenReturn(new TaskOutboxSettlementService.FailureResult(true, false, 1, "redis unavailable"));

        assertEquals(1, publisher.publishBatch(10));

        verify(settlements).markFailed(eq(event.getId()), any(UUID.class), any(IllegalStateException.class), eq(12));
        verify(workerWakeup, never()).requestWakeup();
    }

    @Test
    void deadLettersEventAfterMaxAttempts() {
        Task task = new Task(UUID.randomUUID(), UUID.randomUUID(), UUID.randomUUID(), TaskType.PARSE_DOCUMENT);
        TaskOutbox event = new TaskOutbox(UUID.randomUUID(), task.getId(), null, null, null);
        when(outbox.findByIdAndClaimToken(event.getId(), UUID.fromString("00000000-0000-0000-0000-000000000099")))
            .thenReturn(Optional.of(event));
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));
        TaskOutboxSettlementService service = new TaskOutboxSettlementService(outbox, tasks);

        UUID token = UUID.fromString("00000000-0000-0000-0000-000000000099");
        var first = service.markFailed(event.getId(), token, new IllegalStateException("redis unavailable"), 2);
        var second = service.markFailed(event.getId(), token, new IllegalStateException("redis unavailable"), 2);

        assertTrue(first.settled());
        assertTrue(second.deadLettered());
        assertEquals(TaskStatus.FAILED, task.getStatus());
    }
}
