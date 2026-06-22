package com.noteflow.parsing;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentParseResultRepository extends JpaRepository<DocumentParseResult, UUID> {
    Optional<DocumentParseResult> findByDocumentId(UUID documentId);
}
