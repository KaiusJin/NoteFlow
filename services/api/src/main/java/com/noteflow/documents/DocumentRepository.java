package com.noteflow.documents;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface DocumentRepository extends JpaRepository<Document, UUID> {
    List<Document> findByUserIdOrderByCreatedAtDesc(UUID userId);

    List<Document> findByIdInAndUserId(java.util.Collection<UUID> ids, UUID userId);

    @Query(value = """
        SELECT * FROM documents
         WHERE user_id = :userId
         ORDER BY created_at DESC, id DESC
         LIMIT :limit
        """, nativeQuery = true)
    List<Document> findCursorPage(@Param("userId") UUID userId, @Param("limit") int limit);

    @Query(value = """
        SELECT * FROM documents
         WHERE user_id = :userId
           AND (created_at, id) < (:createdAt, :id)
         ORDER BY created_at DESC, id DESC
         LIMIT :limit
        """, nativeQuery = true)
    List<Document> findCursorPageAfter(
        @Param("userId") UUID userId,
        @Param("createdAt") java.time.Instant createdAt,
        @Param("id") UUID id,
        @Param("limit") int limit
    );
}
