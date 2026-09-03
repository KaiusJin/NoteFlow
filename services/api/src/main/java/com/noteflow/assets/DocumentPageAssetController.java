package com.noteflow.assets;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.storage.PngObjectStorage;
import com.noteflow.storage.StoredObject;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentPageAssetController {
    private final DocumentPageAssetRepository assets;
    private final DocumentRepository documents;
    private final LocalWorkspaceService users;
    private final PngObjectStorage storageFiles;

    public DocumentPageAssetController(DocumentPageAssetRepository assets, DocumentRepository documents,
            LocalWorkspaceService users, PngObjectStorage storageFiles) {
        this.assets = assets;
        this.documents = documents;
        this.users = users;
        this.storageFiles = storageFiles;
    }

    @GetMapping("/documents/{documentId}/assets")
    public List<DocumentPageAssetResponse> getDocumentAssets(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return assets.findByDocumentIdOrderByPageNumberAsc(documentId).stream()
            .map(DocumentPageAssetResponse::from)
            .toList();
    }

    @GetMapping("/assets/{assetId}")
    public ResponseEntity<byte[]> getAsset(@PathVariable UUID assetId) {
        DocumentPageAsset asset = assets.findById(assetId)
            .orElseThrow(() -> new IllegalArgumentException("Asset not found"));
        ensureDocumentAccess(asset.getDocumentId());

        StoredObject object = storageFiles.readPng(asset.getImagePath());
        return ResponseEntity.ok()
            .contentType(MediaType.parseMediaType(object.contentType()))
            .body(object.content());
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
