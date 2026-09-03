package com.noteflow.tasks;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.noteflow.queue.DocumentTaskQueue;
import java.time.Duration;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

class WorkerWakeupCoordinatorTest {
    private WorkerJobLauncher launcher;
    private StringRedisTemplate redis;
    private ValueOperations<String, String> values;
    private DocumentTaskQueue queue;
    private WorkerWakeupCoordinator coordinator;

    @BeforeEach
    void setUp() {
        launcher = mock(WorkerJobLauncher.class);
        redis = mock(StringRedisTemplate.class);
        @SuppressWarnings("unchecked")
        ValueOperations<String, String> mockedValues = mock(ValueOperations.class);
        values = mockedValues;
        queue = mock(DocumentTaskQueue.class);
        when(redis.opsForValue()).thenReturn(values);
        when(launcher.configured()).thenReturn(true);
        when(launcher.coordinationKey()).thenReturn("worker:wakeup:test");
        coordinator = new WorkerWakeupCoordinator(launcher, redis, queue, 20, Runnable::run);
    }

    @Test
    void coalescesLaunchesWithRedisCooldown() {
        when(values.setIfAbsent(eq("worker:wakeup:test"), eq("requested"), any(Duration.class)))
            .thenReturn(true, false);

        coordinator.requestWakeup();
        coordinator.requestWakeup();

        verify(launcher).launch();
    }

    @Test
    void releasesCooldownWhenLaunchFails() {
        when(values.setIfAbsent(eq("worker:wakeup:test"), eq("requested"), any(Duration.class))).thenReturn(true);
        org.mockito.Mockito.doThrow(new IllegalStateException("control plane unavailable"))
            .when(launcher).launch();

        coordinator.requestWakeup();

        verify(redis).delete("worker:wakeup:test");
    }

    @Test
    void recoveryPollStartsWorkerWhenQueueIsNotEmpty() {
        when(queue.hasPendingTasks()).thenReturn(true);
        when(values.setIfAbsent(eq("worker:wakeup:test"), eq("requested"), any(Duration.class))).thenReturn(true);

        coordinator.recoverQueuedWork();

        verify(launcher).launch();
    }

    @Test
    void disabledLauncherDoesNotTouchRedis() {
        when(launcher.configured()).thenReturn(false);

        coordinator.requestWakeup();
        coordinator.recoverQueuedWork();

        verify(redis, never()).opsForValue();
        verify(queue, never()).hasPendingTasks();
    }
}
