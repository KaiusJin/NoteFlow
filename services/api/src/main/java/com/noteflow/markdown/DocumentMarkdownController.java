package com.noteflow.markdown;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.users.DevUserService;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentMarkdownController {
    private final DocumentMarkdownPageRepository pages;
    private final DocumentMarkdownDocumentRepository markdownDocuments;
    private final DocumentRepository documents;
    private final DevUserService users;

    public DocumentMarkdownController(DocumentMarkdownPageRepository pages,
            DocumentMarkdownDocumentRepository markdownDocuments, DocumentRepository documents, DevUserService users) {
        this.pages = pages;
        this.markdownDocuments = markdownDocuments;
        this.documents = documents;
        this.users = users;
    }

    @GetMapping("/documents/{documentId}/markdown-pages")
    public List<DocumentMarkdownPageResponse> getMarkdownPages(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return pages.findByDocumentIdOrderByPageNumberAsc(documentId).stream()
            .map(DocumentMarkdownPageResponse::from)
            .toList();
    }

    @GetMapping("/documents/{documentId}/markdown")
    public DocumentMarkdownDocumentResponse getMarkdownDocument(@PathVariable UUID documentId) {
        ensureDocumentAccess(documentId);
        return markdownDocuments.findByDocumentId(documentId)
            .map(DocumentMarkdownDocumentResponse::from)
            .orElseThrow(() -> new IllegalArgumentException("Markdown document not found"));
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
