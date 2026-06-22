package com.noteflow.vision;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentVisualRegionRepository extends JpaRepository<DocumentVisualRegion, UUID> {
    List<DocumentVisualRegion> findByDocumentIdOrderByPageNumberAscRegionIndexAsc(UUID documentId);
}
