package com.noteflow.tasks;

import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
public class TaskController {
    private final TaskRepository tasks;
    private final LocalWorkspaceService users;
    private final TaskEventStream events;

    public TaskController(TaskRepository tasks, LocalWorkspaceService users, TaskEventStream events) {
        this.tasks = tasks;
        this.users = users;
        this.events = events;
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

    @GetMapping("/tasks")
    public List<TaskResponse> listAll() {
        return events.currentTasks();
    }

    @GetMapping(value = "/events/tasks", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter taskEvents() {
        return events.subscribe();
    }
}
