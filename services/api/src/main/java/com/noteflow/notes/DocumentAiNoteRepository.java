package com.noteflow.notes;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentAiNoteRepository extends JpaRepository<DocumentAiNote, UUID> {
    List<DocumentAiNote> findByDocumentIdOrderByNoteVersionDesc(UUID documentId);
    List<DocumentAiNote> findByDocumentIdInOrderByDocumentIdAscNoteVersionDesc(List<UUID> documentIds);
    Optional<DocumentAiNote> findFirstByDocumentIdOrderByNoteVersionDesc(UUID documentId);
    Optional<DocumentAiNote> findFirstByDocumentIdAndStatusOrderByNoteVersionDesc(UUID documentId, String status);
}
