package com.noteflow.documents;

import java.util.List;
import java.util.UUID;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
public class DocumentController {
    private final DocumentService documents;

    public DocumentController(DocumentService documents) {
        this.documents = documents;
    }

    @PostMapping(value = "/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public CreateDocumentResponse upload(
            @RequestPart("file") MultipartFile file,
            @RequestParam(value = "documentType", required = false) DocumentType documentType,
            @RequestParam(value = "title", required = false) String title) {
        return documents.upload(file, documentType, title);
    }

    @GetMapping("/documents")
    public List<DocumentResponse> list() {
        return documents.listCurrentUserDocuments();
    }

    @GetMapping("/documents/{id}")
    public DocumentResponse get(@PathVariable UUID id) {
        return documents.getCurrentUserDocument(id);
    }
}
