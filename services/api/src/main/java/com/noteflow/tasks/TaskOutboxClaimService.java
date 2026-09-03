package com.noteflow.tasks;

import jakarta.persistence.EntityManager;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TaskOutboxClaimService {
    private final EntityManager entityManager;

    public TaskOutboxClaimService(EntityManager entityManager) {
        this.entityManager = entityManager;
    }

    @Transactional
    @SuppressWarnings("unchecked")
    public List<TaskOutbox> claimBatch(UUID claimToken, int limit, int claimTimeoutSeconds) {
        return entityManager.createNativeQuery("""
            WITH candidates AS (
              SELECT id
              FROM task_outbox
              WHERE published_at IS NULL
                AND dead_letter_at IS NULL
                AND available_at <= NOW()
                AND (
                  claim_token IS NULL
                  OR claimed_at < NOW() - (CAST(:claimTimeoutSeconds AS text) || ' seconds')::interval
                )
              ORDER BY created_at
              LIMIT :limit
              FOR UPDATE SKIP LOCKED
            )
            UPDATE task_outbox event
            SET claim_token=:claimToken, claimed_at=NOW()
            FROM candidates
            WHERE event.id=candidates.id
            RETURNING event.*
            """, TaskOutbox.class)
            .setParameter("claimToken", claimToken)
            .setParameter("limit", Math.max(1, limit))
            .setParameter("claimTimeoutSeconds", Math.max(5, claimTimeoutSeconds))
            .getResultList();
    }
}
