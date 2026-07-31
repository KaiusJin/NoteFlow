package com.noteflow.tasks;

import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TaskDispatchService {
    private static final Set<TaskStatus> ACTIVE_STATUSES = Set.of(
        TaskStatus.PENDING,
        TaskStatus.PROCESSING,
        TaskStatus.RETRYING
    );

    private final TaskRepository tasks;
    private final TaskOutboxRepository outbox;

    public TaskDispatchService(TaskRepository tasks, TaskOutboxRepository outbox) {
        this.tasks = tasks;
        this.outbox = outbox;
    }

    @Transactional
    public Task createAndEnqueue(UUID documentId, UUID userId, TaskType taskType) {
        return createAndEnqueue(documentId, userId, taskType, null);
    }

    @Transactional
    public Task createAndEnqueue(UUID documentId, UUID userId, TaskType taskType, UUID attemptId) {
        Task task = new Task(UUID.randomUUID(), documentId, userId, taskType);
        tasks.save(task);
        outbox.save(new TaskOutbox(UUID.randomUUID(), task.getId(), attemptId, null, null));
        return task;
    }

    @Transactional
    public Task createConversationAndEnqueue(UUID userId, UUID conversationId, UUID messageId) {
        Task task = new Task(UUID.randomUUID(), null, userId, TaskType.ANSWER_CONVERSATION_TURN);
        tasks.save(task);
        outbox.save(new TaskOutbox(UUID.randomUUID(), task.getId(), null, conversationId, messageId));
        return task;
    }

    public Task latestActiveTask(UUID documentId, TaskType taskType) {
        return tasks.findFirstByDocumentIdAndTaskTypeAndStatusInOrderByCreatedAtDesc(
                documentId,
                taskType,
                ACTIVE_STATUSES.stream().toList()
            )
            .orElse(null);
    }

    public Optional<Task> latestTask(UUID documentId, TaskType taskType) {
        return tasks.findFirstByDocumentIdAndTaskTypeOrderByCreatedAtDesc(documentId, taskType);
    }
}
