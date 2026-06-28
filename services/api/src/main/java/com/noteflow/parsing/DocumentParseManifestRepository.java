package com.noteflow.parsing;

import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentParseManifestRepository extends JpaRepository<DocumentParseManifest, UUID> {
}
