package com.noteflow.vision;

import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentVlmResultRepository extends JpaRepository<DocumentVlmResult, UUID> {
    List<DocumentVlmResult> findByDocumentIdOrderByPageNumberAscRegionIndexAsc(UUID documentId);
    List<DocumentVlmResult> findByDocumentIdOrderByPageNumberAscRegionIndexAsc(UUID documentId, Pageable pageable);
}
