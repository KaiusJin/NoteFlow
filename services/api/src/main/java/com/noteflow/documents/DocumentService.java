package com.noteflow.documents;

import com.noteflow.storage.LocalFileStorageService;
import com.noteflow.storage.StoredFile;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskType;
import com.noteflow.users.DevUserService;
import com.noteflow.notes.DocumentAiNote;
import com.noteflow.notes.DocumentAiNoteRepository;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

@Service
public class DocumentService {
    private final DevUserService users;
    private final DocumentRepository documents;
    private final TaskDispatchService taskDispatcher;
    private final LocalFileStorageService storage;
    private final DocumentAiNoteRepository notes;

    public DocumentService(DevUserService users, DocumentRepository documents, TaskDispatchService taskDispatcher,
            LocalFileStorageService storage, DocumentAiNoteRepository notes) {
        this.users = users;
        this.documents = documents;
        this.taskDispatcher = taskDispatcher;
        this.storage = storage;
        this.notes = notes;
    }

    @Transactional
    public CreateDocumentResponse upload(MultipartFile file, DocumentType documentType, String title) {
        validatePdf(file);
        UUID userId = users.currentUserId();
        UUID documentId = UUID.randomUUID();
        StoredFile storedFile = storage.savePdf(documentId, file);
        String resolvedTitle = title == null || title.isBlank() ? originalFilename(file) : title.trim();

        Document document = new Document(
            documentId,
            userId,
            resolvedTitle,
            originalFilename(file),
            storedFile.contentType() == null ? "application/pdf" : storedFile.contentType(),
            storedFile.size(),
            storedFile.storagePath(),
            documentType == null ? DocumentType.OTHER : documentType
        );
        documents.save(document);

        Task task = taskDispatcher.createAndEnqueue(document.getId(), userId, TaskType.PARSE_DOCUMENT);
        return new CreateDocumentResponse(document.getId(), task.getId(), document.getStatus());
    }

    public List<DocumentResponse> listCurrentUserDocuments() {
        UUID userId = users.currentUserId();
        return documents.findByUserIdOrderByCreatedAtDesc(userId).stream()
            .map(document -> {
                String aiNoteStatus = notes.findFirstByDocumentIdOrderByNoteVersionDesc(document.getId())
                    .map(DocumentAiNote::getStatus)
                    .orElse("NOT_STARTED");
                return DocumentResponse.from(document, aiNoteStatus);
            })
            .toList();
    }

    public DocumentResponse getCurrentUserDocument(UUID id) {
        UUID userId = users.currentUserId();
        Document document = documents.findById(id)
            .filter(candidate -> candidate.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
        String aiNoteStatus = notes.findFirstByDocumentIdOrderByNoteVersionDesc(document.getId())
            .map(DocumentAiNote::getStatus)
            .orElse("NOT_STARTED");
        return DocumentResponse.from(document, aiNoteStatus);
    }

    private void validatePdf(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw new IllegalArgumentException("PDF file is required");
        }
        String name = originalFilename(file).toLowerCase();
        if (!name.endsWith(".pdf")) {
            throw new IllegalArgumentException("Only PDF uploads are supported");
        }
    }

    private String originalFilename(MultipartFile file) {
        return file.getOriginalFilename() == null ? "untitled.pdf" : file.getOriginalFilename();
    }
}
