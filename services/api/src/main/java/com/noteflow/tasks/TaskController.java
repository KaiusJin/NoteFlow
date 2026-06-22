package com.noteflow.tasks;

import com.noteflow.users.DevUserService;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class TaskController {
    private final TaskRepository tasks;
    private final DevUserService users;

    public TaskController(TaskRepository tasks, DevUserService users) {
        this.tasks = tasks;
        this.users = users;
    }

    @GetMapping("/tasks/{id}")
    public TaskResponse get(@PathVariable UUID id) {
        UUID userId = users.currentUserId();
        Task task = tasks.findById(id)
            .filter(candidate -> candidate.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Task not found"));
        return TaskResponse.from(task);
    }

    @GetMapping("/documents/{documentId}/tasks")
    public List<TaskResponse> listForDocument(@PathVariable UUID documentId) {
        UUID userId = users.currentUserId();
        return tasks.findByDocumentIdOrderByCreatedAtDesc(documentId).stream()
            .filter(task -> task.getUserId().equals(userId))
            .map(TaskResponse::from)
            .toList();
    }
}
