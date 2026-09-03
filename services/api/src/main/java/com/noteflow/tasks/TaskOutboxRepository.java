package com.noteflow.tasks;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.data.jpa.repository.Lock;
import jakarta.persistence.LockModeType;
import java.util.Optional;

public interface TaskOutboxRepository extends JpaRepository<TaskOutbox, UUID> {

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    Optional<TaskOutbox> findByIdAndClaimToken(UUID id, UUID claimToken);

    @Modifying
    @Query("DELETE FROM TaskOutbox event WHERE event.publishedAt < :cutoff")
    int deletePublishedBefore(@Param("cutoff") Instant cutoff);
}
