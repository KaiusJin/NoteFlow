package com.noteflow.parsing;

import com.noteflow.chunks.DocumentChunkRepository;
import com.noteflow.chunks.DocumentChunkResponse;
import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.users.DevUserService;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentParseController {
    private final DocumentRepository documents;
    private final DocumentParseResultRepository parseResults;
    private final DocumentChunkRepository chunks;
    private final DocumentParseManifestRepository parseManifests;
    private final DevUserService users;

    public DocumentParseController(DocumentRepository documents, DocumentParseResultRepository parseResults,
            DocumentChunkRepository chunks, DocumentParseManifestRepository parseManifests, DevUserService users) {
        this.documents = documents;
        this.parseResults = parseResults;
        this.chunks = chunks;
        this.parseManifests = parseManifests;
        this.users = users;
    }

    @GetMapping("/documents/{documentId}/parse-manifest")
    public DocumentParseManifestResponse getParseManifest(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return parseManifests.findById(documentId)
            .map(DocumentParseManifestResponse::from)
            .orElseThrow(() -> new IllegalArgumentException("Parse manifest not found"));
    }

    @GetMapping("/documents/{documentId}/parse-result")
    public DocumentParseResultResponse getParseResult(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return parseResults.findByDocumentId(documentId)
            .map(DocumentParseResultResponse::from)
            .orElseThrow(() -> new IllegalArgumentException("Parse result not found"));
    }

    @GetMapping("/documents/{documentId}/chunks")
    public List<DocumentChunkResponse> getChunks(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return chunks.findByDocumentIdOrderByChunkIndexAsc(documentId).stream()
            .map(DocumentChunkResponse::from)
            .toList();
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
