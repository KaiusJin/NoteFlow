package com.noteflow.library;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface FolderRepository extends JpaRepository<Folder, UUID> {
    List<Folder> findByUserIdOrderByNameAsc(UUID userId);
    Optional<Folder> findByIdAndUserId(UUID id, UUID userId);
    List<Folder> findByParentId(UUID parentId);
}
