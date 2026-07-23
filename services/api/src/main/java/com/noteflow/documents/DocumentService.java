package com.noteflow.documents;

import com.noteflow.storage.LocalFileStorageService;
import com.noteflow.storage.StoredFile;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskStatus;
import com.noteflow.tasks.TaskType;
import com.noteflow.workspace.LocalWorkspaceService;
import com.noteflow.notes.DocumentAiNote;
import com.noteflow.notes.DocumentAiNoteRepository;
import com.noteflow.tasks.TaskRepository;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

@Service
public class DocumentService {
    private final LocalWorkspaceService users;
    private final DocumentRepository documents;
    private final TaskDispatchService taskDispatcher;
    private final LocalFileStorageService storage;
    private final DocumentAiNoteRepository notes;
    private final TaskRepository taskRepository;
    private final JdbcTemplate jdbc;

    public DocumentService(LocalWorkspaceService users, DocumentRepository documents, TaskDispatchService taskDispatcher,
            LocalFileStorageService storage, DocumentAiNoteRepository notes, TaskRepository taskRepository, JdbcTemplate jdbc) {
        this.users = users;
        this.documents = documents;
        this.taskDispatcher = taskDispatcher;
        this.storage = storage;
        this.notes = notes;
        this.taskRepository = taskRepository;
        this.jdbc = jdbc;
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
        List<Document> userDocuments = documents.findByUserIdOrderByCreatedAtDesc(userId);
        List<UUID> documentIds = userDocuments.stream().map(Document::getId).toList();
        Map<UUID, String> aiNoteStatuses = latestAiNoteStatuses(documentIds);
        Map<UUID, String> embeddingStatuses = embeddingStatuses(documentIds);
        return userDocuments.stream()
            .map(document -> DocumentResponse.from(
                document,
                aiNoteStatuses.getOrDefault(document.getId(), "NOT_STARTED"),
                embeddingStatuses.getOrDefault(document.getId(), "NOT_STARTED")
            ))
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
        return DocumentResponse.from(document, aiNoteStatus, embeddingStatus(document.getId()));
    }

    private String embeddingStatus(UUID documentId) {
        Task activeTask = taskDispatcher.latestActiveTask(documentId, TaskType.GENERATE_EMBEDDINGS);
        if (activeTask != null) {
            return "PROCESSING";
        }
        try {
            Integer count = jdbc.queryForObject(
                "SELECT COUNT(*) FROM document_embeddings WHERE document_id = ? AND embedding IS NOT NULL",
                Integer.class,
                documentId
            );
            if (count != null && count > 0) {
                return "READY";
            }
        } catch (DataAccessException ignored) {
            return "NOT_STARTED";
        }
        return latestEmbeddingTaskStatus(documentId);
    }

    private Map<UUID, String> latestAiNoteStatuses(List<UUID> documentIds) {
        if (documentIds.isEmpty()) return Map.of();
        return notes.findByDocumentIdInOrderByDocumentIdAscNoteVersionDesc(documentIds).stream()
            .collect(Collectors.toMap(
                DocumentAiNote::getDocumentId,
                DocumentAiNote::getStatus,
                (existing, ignored) -> existing
            ));
    }

    private Map<UUID, String> embeddingStatuses(List<UUID> documentIds) {
        if (documentIds.isEmpty()) return Map.of();
        Map<UUID, String> result = taskRepository.findByDocumentIdInAndTaskTypeOrderByCreatedAtDesc(
                documentIds,
                TaskType.GENERATE_EMBEDDINGS
            ).stream()
            .collect(Collectors.toMap(
                Task::getDocumentId,
                task -> activeEmbeddingStatuses().contains(task.getStatus())
                    ? "PROCESSING"
                    : task.getStatus() == TaskStatus.FAILED ? "FAILED" : "NOT_STARTED",
                (existing, ignored) -> existing
            ));
        for (UUID readyDocumentId : documentsWithEmbeddings(documentIds)) {
            if (!"PROCESSING".equals(result.get(readyDocumentId))) {
                result.put(readyDocumentId, "READY");
            }
        }
        return result;
    }

    private Set<TaskStatus> activeEmbeddingStatuses() {
        return Set.of(TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.RETRYING);
    }

    private Set<UUID> documentsWithEmbeddings(List<UUID> documentIds) {
        if (documentIds.isEmpty()) return Set.of();
        String placeholders = String.join(",", java.util.Collections.nCopies(documentIds.size(), "?"));
        try {
            return jdbc.queryForList(
                    "SELECT document_id FROM document_embeddings WHERE document_id IN (" + placeholders + ") AND embedding IS NOT NULL GROUP BY document_id",
                    UUID.class,
                    documentIds.toArray()
                )
                .stream()
                .collect(Collectors.toSet());
        } catch (DataAccessException ignored) {
            return Set.of();
        }
    }

    private String latestEmbeddingTaskStatus(UUID documentId) {
        return taskDispatcher.latestTask(documentId, TaskType.GENERATE_EMBEDDINGS)
            .map(Task::getStatus)
            .map(status -> status == TaskStatus.FAILED ? "FAILED" : "NOT_STARTED")
            .orElse("NOT_STARTED");
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
