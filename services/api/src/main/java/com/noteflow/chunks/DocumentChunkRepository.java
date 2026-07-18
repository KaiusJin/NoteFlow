package com.noteflow.chunks;

import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentChunkRepository extends JpaRepository<DocumentChunk, UUID> {
    List<DocumentChunk> findByDocumentIdOrderByChunkIndexAsc(UUID documentId);
    List<DocumentChunk> findByDocumentIdOrderByChunkIndexAsc(UUID documentId, Pageable pageable);
}
