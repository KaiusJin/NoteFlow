package com.noteflow.markdown;

import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentMarkdownPageRepository extends JpaRepository<DocumentMarkdownPage, UUID> {
    List<DocumentMarkdownPage> findByDocumentIdOrderByPageNumberAsc(UUID documentId);
    List<DocumentMarkdownPage> findByDocumentIdOrderByPageNumberAsc(UUID documentId, Pageable pageable);
}
