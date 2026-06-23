package com.noteflow.tasks;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TaskRepository extends JpaRepository<Task, UUID> {
    List<Task> findByDocumentIdOrderByCreatedAtDesc(UUID documentId);
    List<Task> findByUserIdOrderByCreatedAtDesc(UUID userId);
}
