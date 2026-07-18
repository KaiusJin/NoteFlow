package com.noteflow.layout;

import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentLayoutBlockRepository extends JpaRepository<DocumentLayoutBlock, UUID> {
    List<DocumentLayoutBlock> findByDocumentIdOrderByPageNumberAscBlockIndexAsc(UUID documentId);
    List<DocumentLayoutBlock> findByDocumentIdOrderByPageNumberAscBlockIndexAsc(UUID documentId, Pageable pageable);
}
