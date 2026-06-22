package com.noteflow.assets;

import java.util.List;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentPageAssetRepository extends JpaRepository<DocumentPageAsset, UUID> {
    List<DocumentPageAsset> findByDocumentIdOrderByPageNumberAsc(UUID documentId);
}
