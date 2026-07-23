package com.noteflow.editor;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.library.Note;
import com.noteflow.library.NoteRepository;
import com.noteflow.learningmemory.LearningMemoryService;
import com.noteflow.markdown.DocumentMarkdownDocumentRepository;
import com.noteflow.notes.DocumentAiNoteRepository;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Per-document editable note API, now backed by the unified {@code notes}
 * table: the document's editable note is the note whose
 * {@code sourceDocumentId} equals the document id.
 */
@Service
public class DocumentEditableNoteService {
    private final LocalWorkspaceService users;
    private final DocumentRepository documents;
    private final NoteRepository notes;
    private final DocumentMarkdownDocumentRepository markdownDocuments;
    private final DocumentAiNoteRepository aiNotes;
    private final LearningMemoryService learningMemory;

    public DocumentEditableNoteService(LocalWorkspaceService users, DocumentRepository documents,
            NoteRepository notes, DocumentMarkdownDocumentRepository markdownDocuments,
            DocumentAiNoteRepository aiNotes,LearningMemoryService learningMemory) {
        this.users = users;
        this.documents = documents;
        this.notes = notes;
        this.markdownDocuments = markdownDocuments;
        this.aiNotes = aiNotes;
        this.learningMemory = learningMemory;
    }

    public Optional<DocumentEditableNoteResponse> latest(UUID documentId) {
        UUID userId = users.currentUserId();
        loadCurrentUserDocument(documentId, userId);
        learningMemory.recordDocumentActivity(documentId,"NOTE_OPENED","note-open:"+documentId+":"+(System.currentTimeMillis()/60_000));
        return notes.findFirstBySourceDocumentIdOrderByUpdatedAtDesc(documentId)
            .map(DocumentEditableNoteResponse::fromNote);
    }

    @Transactional
    public DocumentEditableNoteResponse initialize(UUID documentId, String sourceKind) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        String kind = normalizeSourceKind(sourceKind);
        String markdown = sourceMarkdown(documentId, kind);
        String title = document.getTitle() + " - My Notes";

        Note note = notes.findFirstBySourceDocumentIdAndSourceKindOrderByCreatedAtAsc(documentId, kind).orElse(null);
        if (note == null) {
            note = new Note(UUID.randomUUID(), userId, null, title, markdown, kind, documentId);
        } else {
            note.reset(title, markdown, kind);
        }
        DocumentEditableNoteResponse response=DocumentEditableNoteResponse.fromNote(notes.save(note));
        learningMemory.recordDocumentActivity(documentId,"NOTE_UPDATED","note-update:"+UUID.randomUUID());
        return response;
    }

    @Transactional
    public DocumentEditableNoteResponse save(UUID documentId, String title, String markdown) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        Note note = notes.findFirstBySourceDocumentIdOrderByUpdatedAtDesc(documentId).orElse(null);
        if (note == null) {
            String noteTitle = title != null && !title.isBlank() ? title : document.getTitle() + " - My Notes";
            note = new Note(UUID.randomUUID(), userId, null, noteTitle, markdown, "BLANK", documentId);
        } else {
            note.update(title, markdown);
        }
        DocumentEditableNoteResponse response=DocumentEditableNoteResponse.fromNote(notes.save(note));
        learningMemory.recordDocumentActivity(documentId,"NOTE_UPDATED","note-update:"+UUID.randomUUID());
        return response;
    }

    private String sourceMarkdown(UUID documentId, String kind) {
        return switch (kind) {
            case "RAW" -> markdownDocuments.findByDocumentId(documentId)
                .map(markdownDocument -> markdownDocument.getMarkdown() == null ? "" : markdownDocument.getMarkdown())
                .orElseThrow(() -> new IllegalArgumentException("Markdown document not found"));
            case "AI_NOTE" -> aiNotes.findFirstByDocumentIdAndStatusOrderByNoteVersionDesc(documentId, "READY")
                .map(note -> note.getMarkdown() == null ? "" : note.getMarkdown())
                .orElseThrow(() -> new IllegalArgumentException("No READY AI note found for this document"));
            default -> "";
        };
    }

    private String normalizeSourceKind(String sourceKind) {
        String kind = sourceKind == null ? "BLANK" : sourceKind.trim().toUpperCase();
        if (!kind.equals("RAW") && !kind.equals("AI_NOTE") && !kind.equals("BLANK")) {
            throw new IllegalArgumentException("source must be RAW, AI_NOTE, or BLANK");
        }
        return kind;
    }

    private Document loadCurrentUserDocument(UUID documentId, UUID userId) {
        return documents.findById(documentId)
            .filter(candidate -> candidate.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
    }
}
