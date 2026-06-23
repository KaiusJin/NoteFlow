package com.noteflow.notes;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.tasks.Task;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.tasks.TaskType;
import com.noteflow.users.DevUserService;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DocumentAiNoteService {
    private final DevUserService users;
    private final DocumentRepository documents;
    private final DocumentAiNoteRepository notes;
    private final DocumentAiNoteSectionRepository sections;
    private final TaskDispatchService taskDispatcher;

    public DocumentAiNoteService(DevUserService users, DocumentRepository documents, DocumentAiNoteRepository notes,
            DocumentAiNoteSectionRepository sections, TaskDispatchService taskDispatcher) {
        this.users = users;
        this.documents = documents;
        this.notes = notes;
        this.sections = sections;
        this.taskDispatcher = taskDispatcher;
    }

    @Transactional
    public GenerateNotesResponse generate(UUID documentId) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        if (document.getStatus() != DocumentStatus.READY) {
            throw new IllegalArgumentException("Document must be READY before generating notes");
        }
        var existingGenerating = notes.findFirstByDocumentIdAndStatusOrderByNoteVersionDesc(documentId, "GENERATING");
        var existingTask = taskDispatcher.latestActiveTask(documentId, TaskType.GENERATE_NOTES);
        if (existingGenerating.isPresent() && existingTask != null) {
            return new GenerateNotesResponse(existingGenerating.get().getId(), existingTask.getId(), existingGenerating.get().getStatus());
        }
        if (existingGenerating.isPresent()) {
            Task task = taskDispatcher.createAndEnqueue(documentId, userId, TaskType.GENERATE_NOTES);
            return new GenerateNotesResponse(existingGenerating.get().getId(), task.getId(), existingGenerating.get().getStatus());
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

        Task task = taskDispatcher.createAndEnqueue(documentId, userId, TaskType.GENERATE_NOTES);
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
