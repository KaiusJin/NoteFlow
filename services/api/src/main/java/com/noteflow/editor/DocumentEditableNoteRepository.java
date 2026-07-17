package com.noteflow.editor;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentEditableNoteRepository extends JpaRepository<DocumentEditableNote, UUID> {
    Optional<DocumentEditableNote> findByDocumentId(UUID documentId);
}
