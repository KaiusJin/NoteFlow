package com.noteflow.notes;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.queue.DocumentTaskQueue;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskRepository;
import com.noteflow.tasks.TaskStatus;
import com.noteflow.tasks.TaskType;
import com.noteflow.users.DevUserService;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DocumentAiNoteService {
    private final DevUserService users;
    private final DocumentRepository documents;
    private final DocumentAiNoteRepository notes;
    private final DocumentAiNoteSectionRepository sections;
    private final TaskRepository tasks;
    private final DocumentTaskQueue queue;

    public DocumentAiNoteService(DevUserService users, DocumentRepository documents, DocumentAiNoteRepository notes,
            DocumentAiNoteSectionRepository sections, TaskRepository tasks, DocumentTaskQueue queue) {
        this.users = users;
        this.documents = documents;
        this.notes = notes;
        this.sections = sections;
        this.tasks = tasks;
        this.queue = queue;
    }

    @Transactional
    public GenerateNotesResponse generate(UUID documentId) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        if (document.getStatus() != DocumentStatus.READY) {
            throw new IllegalArgumentException("Document must be READY before generating notes");
        }
        var existingGenerating = notes.findFirstByDocumentIdAndStatusOrderByNoteVersionDesc(documentId, "GENERATING");
        var existingTask = latestActiveNotesTask(documentId);
        if (existingGenerating.isPresent() && existingTask != null) {
            return new GenerateNotesResponse(existingGenerating.get().getId(), existingTask.getId(), existingGenerating.get().getStatus());
        }
        int nextVersion = notes.findByDocumentIdOrderByNoteVersionDesc(documentId).stream()
            .findFirst()
            .map(note -> note.getNoteVersion() + 1)
            .orElse(1);
        DocumentAiNote note = new DocumentAiNote(
            UUID.randomUUID(),
            documentId,
            nextVersion,
            document.getTitle() + " - AI Notes"
        );
        notes.save(note);

        Task task = new Task(UUID.randomUUID(), documentId, userId, TaskType.GENERATE_NOTES);
        tasks.save(task);
        queue.enqueue(task);
        return new GenerateNotesResponse(note.getId(), task.getId(), note.getStatus());
    }

    public DocumentAiNoteResponse latest(UUID documentId) {
        UUID userId = users.currentUserId();
        loadCurrentUserDocument(documentId, userId);
        DocumentAiNote note = notes.findFirstByDocumentIdAndStatusOrderByNoteVersionDesc(documentId, "READY")
            .or(() -> notes.findFirstByDocumentIdOrderByNoteVersionDesc(documentId))
            .orElseThrow(() -> new IllegalArgumentException("Notes not found"));
        return DocumentAiNoteResponse.from(note);
    }

    private Task latestActiveNotesTask(UUID documentId) {
        Set<TaskStatus> activeStatuses = Set.of(TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.RETRYING);
        return tasks.findByDocumentIdOrderByCreatedAtDesc(documentId).stream()
            .filter(task -> task.getTaskType() == TaskType.GENERATE_NOTES)
            .filter(task -> activeStatuses.contains(task.getStatus()))
            .findFirst()
            .orElse(null);
    }

    public List<DocumentAiNoteSectionResponse> sections(UUID noteId) {
        UUID userId = users.currentUserId();
        DocumentAiNote note = notes.findById(noteId)
            .orElseThrow(() -> new IllegalArgumentException("Notes not found"));
        loadCurrentUserDocument(note.getDocumentId(), userId);
        return sections.findByNoteIdOrderBySectionIndexAsc(noteId).stream()
            .map(DocumentAiNoteSectionResponse::from)
            .toList();
    }

    private Document loadCurrentUserDocument(UUID documentId, UUID userId) {
        return documents.findById(documentId)
            .filter(candidate -> candidate.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
    }
}
