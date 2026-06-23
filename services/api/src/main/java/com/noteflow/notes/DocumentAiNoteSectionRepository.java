package com.noteflow.notes;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentAiNoteSectionRepository extends JpaRepository<DocumentAiNoteSection, UUID> {
    List<DocumentAiNoteSection> findByNoteIdOrderBySectionIndexAsc(UUID noteId);
}
