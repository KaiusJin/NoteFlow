package com.noteflow.vision;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.users.DevUserService;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
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
    private final DevUserService users;

    public DocumentVisionController(DocumentVisualRegionRepository regions, DocumentVlmResultRepository vlmResults,
            DocumentRepository documents, DevUserService users) {
        this.regions = regions;
        this.vlmResults = vlmResults;
        this.documents = documents;
        this.users = users;
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
    public ResponseEntity<Resource> getRegionAsset(@PathVariable UUID regionId) {
        DocumentVisualRegion region = regions.findById(regionId)
            .orElseThrow(() -> new IllegalArgumentException("Visual region not found"));
        ensureDocumentAccess(region.getDocumentId());

        FileSystemResource resource = new FileSystemResource(Path.of(region.getAssetPath()));
        if (!resource.exists()) {
            throw new IllegalArgumentException("Visual region asset not found");
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

    private int safeLimit(Integer value, int maximum) {
        return Math.max(1, Math.min(maximum, value == null ? maximum : value));
    }
}
