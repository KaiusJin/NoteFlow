package com.noteflow.assets;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.users.DevUserService;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentPageAssetController {
    private final DocumentPageAssetRepository assets;
    private final DocumentRepository documents;
    private final DevUserService users;

    public DocumentPageAssetController(DocumentPageAssetRepository assets, DocumentRepository documents,
            DevUserService users) {
        this.assets = assets;
        this.documents = documents;
        this.users = users;
    }

    @GetMapping("/documents/{documentId}/assets")
    public List<DocumentPageAssetResponse> getDocumentAssets(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return assets.findByDocumentIdOrderByPageNumberAsc(documentId).stream()
            .map(DocumentPageAssetResponse::from)
            .toList();
    }

    @GetMapping("/assets/{assetId}")
    public ResponseEntity<Resource> getAsset(@PathVariable UUID assetId) {
        DocumentPageAsset asset = assets.findById(assetId)
            .orElseThrow(() -> new IllegalArgumentException("Asset not found"));
        ensureDocumentAccess(asset.getDocumentId());

        FileSystemResource resource = new FileSystemResource(Path.of(asset.getImagePath()));
        if (!resource.exists()) {
            throw new IllegalArgumentException("Asset file not found");
        }
        return ResponseEntity.ok()
            .contentType(MediaType.IMAGE_PNG)
            .body(resource);
    }

    private void ensureDocumentAccess(UUID documentId) {
        UUID userId = users.currentUserId();
        Document document = documents.findById(documentId)
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
        if (!document.getUserId().equals(userId)) {
            throw new IllegalArgumentException("Document not found");
        }
    }
}
