package com.noteflow.library;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface NoteRepository extends JpaRepository<Note, UUID> {
    List<Note> findByUserIdOrderByUpdatedAtDesc(UUID userId);

    @Query(value = """
        SELECT * FROM notes
         WHERE user_id = :userId
         ORDER BY updated_at DESC, id DESC
         LIMIT :limit
        """, nativeQuery = true)
    List<Note> findCursorPage(@Param("userId") UUID userId, @Param("limit") int limit);

    @Query(value = """
        SELECT * FROM notes
         WHERE user_id = :userId
           AND (updated_at, id) < (:updatedAt, :id)
         ORDER BY updated_at DESC, id DESC
         LIMIT :limit
        """, nativeQuery = true)
    List<Note> findCursorPageAfter(
        @Param("userId") UUID userId,
        @Param("updatedAt") Instant updatedAt,
        @Param("id") UUID id,
        @Param("limit") int limit
    );
    Optional<Note> findByIdAndUserId(UUID id, UUID userId);
    List<Note> findByFolderId(UUID folderId);
    Optional<Note> findFirstBySourceDocumentIdOrderByUpdatedAtDesc(UUID sourceDocumentId);
    Optional<Note> findFirstBySourceDocumentIdAndSourceKindOrderByCreatedAtAsc(UUID sourceDocumentId, String sourceKind);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query(value = """
        UPDATE notes
           SET title = CASE
                         WHEN :title IS NULL OR BTRIM(:title) = '' THEN title
                         ELSE :title
                       END,
               markdown = :markdown,
               updated_at = :updatedAt
         WHERE id = :id
           AND user_id = :userId
           AND updated_at = :expectedUpdatedAt
        """, nativeQuery = true)
    int updateContentIfUnchanged(
        @Param("id") UUID id,
        @Param("userId") UUID userId,
        @Param("title") String title,
        @Param("markdown") String markdown,
        @Param("expectedUpdatedAt") Instant expectedUpdatedAt,
        @Param("updatedAt") Instant updatedAt
    );

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query(value = """
        UPDATE notes
           SET title = :title,
               updated_at = :updatedAt
         WHERE id = :id
           AND user_id = :userId
           AND updated_at = :expectedUpdatedAt
        """, nativeQuery = true)
    int renameIfUnchanged(
        @Param("id") UUID id,
        @Param("userId") UUID userId,
        @Param("title") String title,
        @Param("expectedUpdatedAt") Instant expectedUpdatedAt,
        @Param("updatedAt") Instant updatedAt
    );
}
