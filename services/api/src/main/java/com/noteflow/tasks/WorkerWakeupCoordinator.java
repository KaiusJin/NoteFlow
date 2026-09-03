package com.noteflow.tasks;

import com.noteflow.queue.DocumentTaskQueue;
import jakarta.annotation.PreDestroy;
import java.time.Duration;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class WorkerWakeupCoordinator {
    private static final Logger log = LoggerFactory.getLogger(WorkerWakeupCoordinator.class);

    private final WorkerJobLauncher launcher;
    private final StringRedisTemplate redis;
    private final DocumentTaskQueue queue;
    private final Duration cooldown;
    private final Executor executor;
    private final ExecutorService ownedExecutor;

    public WorkerWakeupCoordinator(
            WorkerJobLauncher launcher,
            StringRedisTemplate redis,
            DocumentTaskQueue queue,
            @Value("${noteflow.worker.wakeup-cooldown-seconds:20}") int cooldownSeconds) {
        this(
            launcher,
            redis,
            queue,
            cooldownSeconds,
            Executors.newSingleThreadExecutor(Thread.ofVirtual().name("worker-wakeup-", 0).factory())
        );
    }

    WorkerWakeupCoordinator(
            WorkerJobLauncher launcher,
            StringRedisTemplate redis,
            DocumentTaskQueue queue,
            int cooldownSeconds,
            Executor executor) {
        this.launcher = launcher;
        this.redis = redis;
        this.queue = queue;
        this.cooldown = Duration.ofSeconds(Math.max(5, cooldownSeconds));
        this.executor = executor;
        this.ownedExecutor = executor instanceof ExecutorService service ? service : null;
    }

    public void requestWakeup() {
        if (!launcher.configured()) {
            return;
        }

        boolean shouldLaunch;
        try {
            shouldLaunch = Boolean.TRUE.equals(
                redis.opsForValue().setIfAbsent(launcher.coordinationKey(), "requested", cooldown)
            );
        } catch (RuntimeException error) {
            // The task was already durably accepted by the outbox and queue.
            // Prefer a duplicate job invocation over leaving it stranded.
            log.warn("Worker wakeup coordination unavailable; launching without coalescing", error);
            shouldLaunch = true;
        }
        if (!shouldLaunch) {
            return;
        }
        try {
            executor.execute(this::launchSafely);
        } catch (RejectedExecutionException error) {
            releaseCooldown();
            log.warn("Worker wakeup rejected during API shutdown", error);
        }
    }

    @Scheduled(
        fixedDelayString = "${noteflow.worker.wakeup-recovery-millis:30000}",
        initialDelayString = "${noteflow.worker.wakeup-recovery-millis:30000}"
    )
    public void recoverQueuedWork() {
        if (!launcher.configured()) {
            return;
        }
        try {
            if (queue.hasPendingTasks()) {
                requestWakeup();
            }
        } catch (RuntimeException error) {
            log.warn("Could not inspect Redis queues for worker wakeup recovery", error);
        }
    }

    private void launchSafely() {
        try {
            launcher.launch();
            log.info("Cloud worker wakeup requested");
        } catch (RuntimeException error) {
            releaseCooldown();
            log.warn("Cloud worker wakeup failed; the recovery poll will retry", error);
        }
    }

    private void releaseCooldown() {
        try {
            redis.delete(launcher.coordinationKey());
        } catch (RuntimeException cleanupError) {
            log.debug("Could not release worker wakeup cooldown", cleanupError);
        }
    }

    @PreDestroy
    public void close() {
        if (ownedExecutor != null) {
            ownedExecutor.shutdown();
        }
    }
}
