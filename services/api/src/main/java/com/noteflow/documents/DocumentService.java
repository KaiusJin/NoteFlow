package com.noteflow.documents;

import com.noteflow.storage.DocumentObjectStorage;
import com.noteflow.storage.StoredFile;
import com.noteflow.common.CursorPage;
import com.noteflow.common.OpaqueCursor;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskStatus;
import com.noteflow.tasks.TaskType;
import com.noteflow.workspace.LocalWorkspaceService;
import com.noteflow.notes.DocumentAiNote;
import com.noteflow.notes.DocumentAiNoteRepository;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.multipart.MultipartFile;

@Service
public class DocumentService {
    private static final int PDF_HEADER_SCAN_BYTES = 1024;
    private static final byte[] PDF_SIGNATURE = "%PDF-".getBytes(StandardCharsets.US_ASCII);
    private static final java.util.Set<String> ACTIVE_EMBEDDING_STATUSES = java.util.Set.of(
        TaskStatus.PENDING.name(),
        TaskStatus.PROCESSING.name(),
        TaskStatus.RETRYING.name()
    );
    private final LocalWorkspaceService users;
    private final DocumentRepository documents;
    private final TaskDispatchService taskDispatcher;
    private final DocumentObjectStorage storage;
    private final DocumentAiNoteRepository notes;
    private final JdbcTemplate jdbc;

    public DocumentService(LocalWorkspaceService users, DocumentRepository documents, TaskDispatchService taskDispatcher,
            DocumentObjectStorage storage, DocumentAiNoteRepository notes, JdbcTemplate jdbc) {
        this.users = users;
        this.documents = documents;
        this.taskDispatcher = taskDispatcher;
        this.storage = storage;
        this.notes = notes;
        this.jdbc = jdbc;
    }

    @Transactional
    public CreateDocumentResponse upload(MultipartFile file, DocumentType documentType, String title) {
        validatePdf(file);
        UUID userId = users.currentUserId();
        UUID documentId = UUID.randomUUID();
        StoredFile storedFile = storage.savePdf(userId, documentId, file);
        registerRollbackCleanup(storedFile.storagePath());
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
        return listCurrentUserDocuments(100, null).items();
    }

    public CursorPage<DocumentResponse> listCurrentUserDocuments(int requestedLimit, String rawCursor) {
        UUID userId = users.currentUserId();
        int limit = Math.max(1, Math.min(200, requestedLimit));
        OpaqueCursor cursor = OpaqueCursor.decode(rawCursor);
        List<Document> fetched = cursor == null
            ? documents.findCursorPage(userId, limit + 1)
            : documents.findCursorPageAfter(userId, cursor.timestamp(), cursor.id(), limit + 1);
        boolean hasNext = fetched.size() > limit;
        List<Document> userDocuments = hasNext ? fetched.subList(0, limit) : fetched;
        List<UUID> documentIds = userDocuments.stream().map(Document::getId).toList();
        Map<UUID, String> aiNoteStatuses = latestAiNoteStatuses(documentIds);
        Map<UUID, String> embeddingStatuses = embeddingStatuses(documentIds);
        List<DocumentResponse> items = userDocuments.stream()
            .map(document -> DocumentResponse.from(
                document,
                aiNoteStatuses.getOrDefault(document.getId(), "NOT_STARTED"),
                embeddingStatuses.getOrDefault(document.getId(), "NOT_STARTED")
            ))
            .toList();
        Document last = hasNext ? userDocuments.getLast() : null;
        return new CursorPage<>(
            items,
            last == null ? null : new OpaqueCursor(last.getCreatedAt(), last.getId()).encode()
        );
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
        Integer count = jdbc.queryForObject(
            "SELECT COUNT(*) FROM document_embeddings WHERE document_id = ? AND embedding IS NOT NULL",
            Integer.class,
            documentId
        );
        if (count != null && count > 0) {
            return "READY";
        }
        return latestEmbeddingTaskStatus(documentId);
    }

    private Map<UUID, String> latestAiNoteStatuses(List<UUID> documentIds) {
        if (documentIds.isEmpty()) return Map.of();
        String placeholders = String.join(",", java.util.Collections.nCopies(documentIds.size(), "?"));
        // Aggregate query: only document_id/status of the latest note version
        // per document, no markdown payloads are loaded.
        return jdbc.query(
                "SELECT DISTINCT ON (document_id) document_id, status FROM document_ai_notes "
                    + "WHERE document_id IN (" + placeholders + ") ORDER BY document_id, note_version DESC",
                (rs, rowNum) -> Map.entry(
                    rs.getObject("document_id", UUID.class),
                    rs.getString("status")
                ),
                documentIds.toArray()
            )
            .stream()
            .collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));
    }

    private Map<UUID, String> embeddingStatuses(List<UUID> documentIds) {
        if (documentIds.isEmpty()) return Map.of();
        // Single grouped query combining both the embedding task states and
        // the presence of stored embeddings. Mirrors the previous semantics:
        // the latest GENERATE_EMBEDDINGS task decides PROCESSING/FAILED, and
        // stored embeddings win with READY unless a task is active.
        Map<UUID, String> result = new java.util.LinkedHashMap<>();
        String placeholders = String.join(",", java.util.Collections.nCopies(documentIds.size(), "?"));
        jdbc.query("""
                WITH tasks AS (
                    SELECT DISTINCT ON (document_id) document_id, status
                      FROM tasks
                     WHERE document_id IN (%s) AND task_type = 'GENERATE_EMBEDDINGS'
                     ORDER BY document_id, created_at DESC
                ), embeddings AS (
                    SELECT document_id, TRUE AS has_embedding
                      FROM document_embeddings
                     WHERE document_id IN (%s) AND embedding IS NOT NULL
                     GROUP BY document_id
                )
                SELECT COALESCE(t.document_id, e.document_id) AS document_id,
                       COALESCE(t.status, '') AS status,
                       COALESCE(e.has_embedding, FALSE) AS has_embedding
                  FROM tasks t
                  FULL JOIN embeddings e ON e.document_id = t.document_id
                """.formatted(placeholders, placeholders),
                rs -> {
                    UUID documentId = rs.getObject("document_id", UUID.class);
                    String status = rs.getString("status");
                    if (isActiveEmbeddingStatus(status)) {
                        result.put(documentId, "PROCESSING");
                    } else if (rs.getBoolean("has_embedding")) {
                        result.put(documentId, "READY");
                    } else if (TaskStatus.FAILED.name().equals(status)) {
                        result.put(documentId, "FAILED");
                    } else {
                        result.put(documentId, "NOT_STARTED");
                    }
                },
                // Two placeholder groups: tasks first, then embeddings.
                concat(documentIds, documentIds).toArray()
        );
        return result;
    }

    private static List<UUID> concat(List<UUID> left, List<UUID> right) {
        List<UUID> all = new java.util.ArrayList<>(left);
        all.addAll(right);
        return all;
    }

    static boolean isActiveEmbeddingStatus(String status) {
        return ACTIVE_EMBEDDING_STATUSES.contains(status);
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
        String name = originalFilename(file).toLowerCase(Locale.ROOT);
        if (!name.endsWith(".pdf")) {
            throw new IllegalArgumentException("Only PDF uploads are supported");
        }
        if (!hasPdfSignature(file)) {
            throw new IllegalArgumentException("Uploaded file is not a valid PDF");
        }
    }

    private boolean hasPdfSignature(MultipartFile file) {
        try (InputStream input = file.getInputStream()) {
            byte[] prefix = input.readNBytes(PDF_HEADER_SCAN_BYTES);
            for (int offset = 0; offset <= prefix.length - PDF_SIGNATURE.length; offset++) {
                boolean matches = true;
                for (int index = 0; index < PDF_SIGNATURE.length; index++) {
                    if (prefix[offset + index] != PDF_SIGNATURE[index]) {
                        matches = false;
                        break;
                    }
                }
                if (matches) {
                    return true;
                }
            }
            return false;
        } catch (IOException error) {
            throw new IllegalArgumentException("Could not read uploaded PDF", error);
        }
    }

    private String originalFilename(MultipartFile file) {
        return file.getOriginalFilename() == null ? "untitled.pdf" : file.getOriginalFilename();
    }

    private void registerRollbackCleanup(String storagePath) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            throw new IllegalStateException("Upload persistence requires an active transaction");
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCompletion(int status) {
                if (status == STATUS_ROLLED_BACK) {
                    storage.deleteIfExists(storagePath);
                }
            }
        });
    }
}
