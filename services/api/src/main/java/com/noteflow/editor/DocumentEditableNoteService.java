package com.noteflow.editor;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.markdown.DocumentMarkdownDocumentRepository;
import com.noteflow.notes.DocumentAiNoteRepository;
import com.noteflow.users.DevUserService;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DocumentEditableNoteService {
    private final DevUserService users;
    private final DocumentRepository documents;
    private final DocumentEditableNoteRepository editableNotes;
    private final DocumentMarkdownDocumentRepository markdownDocuments;
    private final DocumentAiNoteRepository aiNotes;

    public DocumentEditableNoteService(DevUserService users, DocumentRepository documents,
            DocumentEditableNoteRepository editableNotes, DocumentMarkdownDocumentRepository markdownDocuments,
            DocumentAiNoteRepository aiNotes) {
        this.users = users;
        this.documents = documents;
        this.editableNotes = editableNotes;
        this.markdownDocuments = markdownDocuments;
        this.aiNotes = aiNotes;
    }

    public Optional<DocumentEditableNoteResponse> latest(UUID documentId) {
        UUID userId = users.currentUserId();
        loadCurrentUserDocument(documentId, userId);
        return editableNotes.findByDocumentId(documentId).map(DocumentEditableNoteResponse::from);
    }

    @Transactional
    public DocumentEditableNoteResponse initialize(UUID documentId, String sourceKind) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        String kind = normalizeSourceKind(sourceKind);
        String markdown = sourceMarkdown(documentId, kind);
        String title = document.getTitle() + " - My Notes";

        DocumentEditableNote note = editableNotes.findByDocumentId(documentId).orElse(null);
        if (note == null) {
            note = new DocumentEditableNote(UUID.randomUUID(), documentId, userId, title, markdown, kind);
        } else {
            note.reset(title, markdown, kind);
        }
        return DocumentEditableNoteResponse.from(editableNotes.save(note));
    }

    @Transactional
    public DocumentEditableNoteResponse save(UUID documentId, String title, String markdown) {
        UUID userId = users.currentUserId();
        Document document = loadCurrentUserDocument(documentId, userId);
        DocumentEditableNote note = editableNotes.findByDocumentId(documentId).orElse(null);
        if (note == null) {
            String noteTitle = title != null && !title.isBlank() ? title : document.getTitle() + " - My Notes";
            note = new DocumentEditableNote(UUID.randomUUID(), documentId, userId, noteTitle, markdown, "BLANK");
        } else {
            note.update(title, markdown);
        }
        return DocumentEditableNoteResponse.from(editableNotes.save(note));
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
