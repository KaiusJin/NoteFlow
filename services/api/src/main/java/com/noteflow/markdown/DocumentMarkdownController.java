package com.noteflow.markdown;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.learningmemory.LearningMemoryService;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentMarkdownController {
    private final DocumentMarkdownPageRepository pages;
    private final DocumentMarkdownDocumentRepository markdownDocuments;
    private final DocumentRepository documents;
    private final LocalWorkspaceService users;
    private final LearningMemoryService learningMemory;

    public DocumentMarkdownController(DocumentMarkdownPageRepository pages,
            DocumentMarkdownDocumentRepository markdownDocuments, DocumentRepository documents, LocalWorkspaceService users,
            LearningMemoryService learningMemory) {
        this.pages = pages;
        this.markdownDocuments = markdownDocuments;
        this.documents = documents;
        this.users = users;
        this.learningMemory = learningMemory;
    }

    @GetMapping("/documents/{documentId}/markdown-pages")
    public List<DocumentMarkdownPageResponse> getMarkdownPages(
            @PathVariable UUID documentId,
            @RequestParam(required = false) Integer limit) {
        ensureDocumentAccess(documentId);
        List<DocumentMarkdownPage> rows = limit == null
            ? pages.findByDocumentIdOrderByPageNumberAsc(documentId)
            : pages.findByDocumentIdOrderByPageNumberAsc(documentId, PageRequest.of(0, safeLimit(limit, 100)));
        return rows.stream()
            .map(DocumentMarkdownPageResponse::from)
            .toList();
    }

    @GetMapping("/documents/{documentId}/markdown")
    public DocumentMarkdownDocumentResponse getMarkdownDocument(
            @PathVariable UUID documentId,
            @RequestParam(required = false) Integer previewChars) {
        ensureDocumentAccess(documentId);
        learningMemory.recordDocumentActivity(documentId,"NOTE_OPENED","note-open:"+documentId+":"+(System.currentTimeMillis()/60_000));
        return markdownDocuments.findByDocumentId(documentId)
            .map(document -> DocumentMarkdownDocumentResponse.from(document, safePreviewChars(previewChars)))
            .orElseThrow(() -> new IllegalArgumentException("Markdown document not found"));
    }

    private int safeLimit(Integer value, int maximum) {
        return Math.max(1, Math.min(maximum, value == null ? maximum : value));
    }

    private int safePreviewChars(Integer value) {
        return value == null ? Integer.MAX_VALUE : Math.max(1, Math.min(200_000, value));
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
