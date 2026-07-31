package com.noteflow.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;

class TaskDispatchServiceTest {

    @Test
    void storesTaskAndOutboxEventTogether() {
        TaskRepository tasks = Mockito.mock(TaskRepository.class);
        TaskOutboxRepository outbox = Mockito.mock(TaskOutboxRepository.class);
        when(tasks.save(any(Task.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(outbox.save(any(TaskOutbox.class))).thenAnswer(invocation -> invocation.getArgument(0));
        TaskDispatchService service = new TaskDispatchService(tasks, outbox);
        UUID documentId = UUID.randomUUID();
        UUID userId = UUID.randomUUID();
        UUID attemptId = UUID.randomUUID();

        Task task = service.createAndEnqueue(documentId, userId, TaskType.GRADE_QUIZ_ATTEMPT, attemptId);

        ArgumentCaptor<TaskOutbox> event = ArgumentCaptor.forClass(TaskOutbox.class);
        verify(outbox).save(event.capture());
        assertEquals(task.getId(), event.getValue().getTaskId());
        assertEquals(attemptId, event.getValue().getAttemptId());
        assertNull(event.getValue().getPublishedAt());
    }
}
