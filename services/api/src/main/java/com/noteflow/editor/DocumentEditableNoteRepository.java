package com.noteflow.editor;

import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentEditableNoteRepository extends JpaRepository<DocumentEditableNote, UUID> {
}
