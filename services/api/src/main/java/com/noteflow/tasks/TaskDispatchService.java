package com.noteflow.tasks;

import com.noteflow.queue.DocumentTaskQueue;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
public class TaskDispatchService {
    private static final Set<TaskStatus> ACTIVE_STATUSES = Set.of(
        TaskStatus.PENDING,
        TaskStatus.PROCESSING,
        TaskStatus.RETRYING
    );

    private final TaskRepository tasks;
    private final DocumentTaskQueue queue;

    public TaskDispatchService(TaskRepository tasks, DocumentTaskQueue queue) {
        this.tasks = tasks;
        this.queue = queue;
    }

    public Task createAndEnqueue(UUID documentId, UUID userId, TaskType taskType) {
        Task task = new Task(UUID.randomUUID(), documentId, userId, taskType);
        tasks.save(task);
        enqueueAfterCommit(task);
        return task;
    }

    public Task latestActiveTask(UUID documentId, TaskType taskType) {
        return tasks.findByDocumentIdOrderByCreatedAtDesc(documentId).stream()
            .filter(task -> task.getTaskType() == taskType)
            .filter(task -> ACTIVE_STATUSES.contains(task.getStatus()))
            .findFirst()
            .orElse(null);
    }

    private void enqueueAfterCommit(Task task) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            queue.enqueue(task);
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                queue.enqueue(task);
            }
        });
    }
}
