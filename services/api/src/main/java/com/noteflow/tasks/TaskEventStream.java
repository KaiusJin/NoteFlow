package com.noteflow.tasks;

import com.noteflow.workspace.LocalWorkspaceService;
import java.io.IOException;
import java.time.Duration;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@Component
public class TaskEventStream {
    private final TaskRepository tasks;
    private final LocalWorkspaceService users;
    private final CopyOnWriteArrayList<SseEmitter> emitters = new CopyOnWriteArrayList<>();
    private volatile int lastSnapshotHash;

    public TaskEventStream(TaskRepository tasks, LocalWorkspaceService users) {
        this.tasks = tasks;
        this.users = users;
    }

    public SseEmitter subscribe() {
        SseEmitter emitter = new SseEmitter(Duration.ofMinutes(30).toMillis());
        emitters.add(emitter);
        emitter.onCompletion(() -> emitters.remove(emitter));
        emitter.onTimeout(() -> emitters.remove(emitter));
        emitter.onError(ignored -> emitters.remove(emitter));
        send(emitter, currentTasks());
        return emitter;
    }

    public List<TaskResponse> currentTasks() {
        UUID userId = users.currentUserId();
        Map<UUID, Task> visible = new LinkedHashMap<>();
        tasks.findByUserIdAndStatusInOrderByCreatedAtDesc(
            userId,
            List.of(TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.RETRYING)
        ).forEach(task -> visible.put(task.getId(), task));
        tasks.findTop100ByUserIdOrderByCreatedAtDesc(userId)
            .forEach(task -> visible.putIfAbsent(task.getId(), task));
        return visible.values().stream()
            .sorted(Comparator.comparing(Task::getCreatedAt).reversed())
            .map(TaskResponse::from)
            .toList();
    }

    @Scheduled(fixedDelayString = "${noteflow.events.task-refresh-millis:1000}")
    void emitChanges() {
        if (emitters.isEmpty()) return;
        List<TaskResponse> snapshot = currentTasks();
        int snapshotHash = snapshot.hashCode();
        if (snapshotHash == lastSnapshotHash) return;
        lastSnapshotHash = snapshotHash;
        for (SseEmitter emitter : emitters) send(emitter, snapshot);
    }

    private void send(SseEmitter emitter, List<TaskResponse> snapshot) {
        try {
            emitter.send(SseEmitter.event().name("tasks").reconnectTime(3_000).data(snapshot));
        } catch (IOException | IllegalStateException error) {
            emitters.remove(emitter);
            emitter.complete();
        }
    }
}
