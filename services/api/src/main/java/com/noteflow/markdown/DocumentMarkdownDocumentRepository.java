package com.noteflow.markdown;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentMarkdownDocumentRepository extends JpaRepository<DocumentMarkdownDocument, UUID> {
    Optional<DocumentMarkdownDocument> findByDocumentId(UUID documentId);
    List<DocumentMarkdownDocument> findByDocumentIdIn(Collection<UUID> documentIds);
}
