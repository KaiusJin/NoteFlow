package com.noteflow.tasks;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface TaskOutboxRepository extends JpaRepository<TaskOutbox, UUID> {

    @Query(value = """
        SELECT *
        FROM task_outbox
        WHERE published_at IS NULL
          AND available_at <= NOW()
        ORDER BY created_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
        """, nativeQuery = true)
    List<TaskOutbox> lockNextBatch(@Param("limit") int limit);

    @Modifying
    @Query("DELETE FROM TaskOutbox event WHERE event.publishedAt < :cutoff")
    int deletePublishedBefore(@Param("cutoff") Instant cutoff);
}
