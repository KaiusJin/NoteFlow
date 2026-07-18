package com.noteflow.layout;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.users.DevUserService;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentLayoutBlockController {
    private final DocumentLayoutBlockRepository blocks;
    private final DocumentRepository documents;
    private final DevUserService users;

    public DocumentLayoutBlockController(DocumentLayoutBlockRepository blocks, DocumentRepository documents,
            DevUserService users) {
        this.blocks = blocks;
        this.documents = documents;
        this.users = users;
    }

    @GetMapping("/documents/{documentId}/layout-blocks")
    public List<DocumentLayoutBlockResponse> getLayoutBlocks(
            @PathVariable UUID documentId,
            @RequestParam(required = false) Integer limit) {
        ensureDocumentAccess(documentId);
        List<DocumentLayoutBlock> rows = limit == null
            ? blocks.findByDocumentIdOrderByPageNumberAscBlockIndexAsc(documentId)
            : blocks.findByDocumentIdOrderByPageNumberAscBlockIndexAsc(documentId, PageRequest.of(0, safeLimit(limit, 500)));
        return rows.stream()
            .map(DocumentLayoutBlockResponse::from)
            .toList();
    }

    private int safeLimit(Integer value, int maximum) {
        return Math.max(1, Math.min(maximum, value == null ? maximum : value));
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
