package com.noteflow.library;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface NoteRepository extends JpaRepository<Note, UUID> {
    List<Note> findByUserIdOrderByUpdatedAtDesc(UUID userId);
    Optional<Note> findByIdAndUserId(UUID id, UUID userId);
    List<Note> findByFolderId(UUID folderId);
    Optional<Note> findFirstBySourceDocumentIdOrderByUpdatedAtDesc(UUID sourceDocumentId);
    Optional<Note> findFirstBySourceDocumentIdAndSourceKindOrderByCreatedAtAsc(UUID sourceDocumentId, String sourceKind);
}
