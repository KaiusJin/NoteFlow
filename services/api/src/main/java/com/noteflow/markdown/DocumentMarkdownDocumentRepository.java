package com.noteflow.markdown;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentMarkdownDocumentRepository extends JpaRepository<DocumentMarkdownDocument, UUID> {
    Optional<DocumentMarkdownDocument> findByDocumentId(UUID documentId);
}
