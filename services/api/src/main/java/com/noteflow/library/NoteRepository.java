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

    /** Bulk "move to Unfiled": detaches every note in the given folders in one statement. */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("UPDATE Note n SET n.folderId = NULL, n.updatedAt = :updatedAt WHERE n.folderId IN :folderIds")
    int clearFolder(@Param("folderIds") java.util.Collection<UUID> folderIds, @Param("updatedAt") Instant updatedAt);

    Optional<Note> findFirstBySourceDocumentIdOrderByUpdatedAtDesc(UUID sourceDocumentId);
    Optional<Note> findFirstBySourceDocumentIdAndSourceKindOrderByCreatedAtAsc(UUID sourceDocumentId, String sourceKind);

    /**
     * Optimistic-concurrency content update. A {@code null} title or markdown
     * means "leave that column unchanged"; non-null values replace the column
     * wholesale (including with an empty string). The WHERE clause on
     * {@code updated_at = :expectedUpdatedAt} keeps the optimistic lock intact.
     *
     * @return 1 when the row was updated, 0 when the note was missing or
     *         concurrently modified
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query(value = """
        UPDATE notes
           SET title = COALESCE(:title, title),
               markdown = COALESCE(:markdown, markdown),
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
