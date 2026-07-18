package com.noteflow.tasks;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TaskRepository extends JpaRepository<Task, UUID> {
    List<Task> findByDocumentIdOrderByCreatedAtDesc(UUID documentId);
    List<Task> findByDocumentIdInAndTaskTypeOrderByCreatedAtDesc(List<UUID> documentIds, TaskType taskType);
    List<Task> findTop100ByUserIdOrderByCreatedAtDesc(UUID userId);
    List<Task> findByUserIdAndStatusInOrderByCreatedAtDesc(UUID userId, List<TaskStatus> statuses);
    java.util.Optional<Task> findFirstByDocumentIdAndTaskTypeOrderByCreatedAtDesc(UUID documentId, TaskType taskType);
    java.util.Optional<Task> findFirstByDocumentIdAndTaskTypeAndStatusInOrderByCreatedAtDesc(
        UUID documentId,
        TaskType taskType,
        List<TaskStatus> statuses
    );
}
