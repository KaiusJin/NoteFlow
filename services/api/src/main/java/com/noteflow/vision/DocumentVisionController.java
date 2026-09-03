package com.noteflow.vision;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.storage.PngObjectStorage;
import com.noteflow.storage.StoredObject;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentVisionController {
    private final DocumentVisualRegionRepository regions;
    private final DocumentVlmResultRepository vlmResults;
    private final DocumentRepository documents;
    private final LocalWorkspaceService users;
    private final PngObjectStorage storageFiles;

    public DocumentVisionController(DocumentVisualRegionRepository regions, DocumentVlmResultRepository vlmResults,
            DocumentRepository documents, LocalWorkspaceService users, PngObjectStorage storageFiles) {
        this.regions = regions;
        this.vlmResults = vlmResults;
        this.documents = documents;
        this.users = users;
        this.storageFiles = storageFiles;
    }

    @GetMapping("/documents/{documentId}/visual-regions")
    public List<DocumentVisualRegionResponse> getVisualRegions(
            @PathVariable UUID documentId,
            @RequestParam(required = false) Integer limit) {
        ensureDocumentAccess(documentId);
        List<DocumentVisualRegion> rows = limit == null
            ? regions.findByDocumentIdOrderByPageNumberAscRegionIndexAsc(documentId)
            : regions.findByDocumentIdOrderByPageNumberAscRegionIndexAsc(documentId, PageRequest.of(0, safeLimit(limit, 200)));
        return rows.stream()
            .map(DocumentVisualRegionResponse::from)
            .toList();
    }

    @GetMapping("/documents/{documentId}/vlm-results")
    public List<DocumentVlmResultResponse> getVlmResults(
            @PathVariable UUID documentId,
            @RequestParam(required = false) Integer limit) {
        ensureDocumentAccess(documentId);
        List<DocumentVlmResult> rows = limit == null
            ? vlmResults.findByDocumentIdOrderByPageNumberAscRegionIndexAsc(documentId)
            : vlmResults.findByDocumentIdOrderByPageNumberAscRegionIndexAsc(documentId, PageRequest.of(0, safeLimit(limit, 200)));
        return rows.stream()
            .map(DocumentVlmResultResponse::from)
            .toList();
    }

    @GetMapping("/visual-regions/{regionId}/asset")
    public ResponseEntity<byte[]> getRegionAsset(@PathVariable UUID regionId) {
        DocumentVisualRegion region = regions.findById(regionId)
            .orElseThrow(() -> new IllegalArgumentException("Visual region not found"));
        ensureDocumentAccess(region.getDocumentId());

        StoredObject object = storageFiles.readPng(region.getAssetPath());
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

    private int safeLimit(Integer value, int maximum) {
        return Math.max(1, Math.min(maximum, value == null ? maximum : value));
    }
}
