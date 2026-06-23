package com.noteflow.notes;

import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentAiNoteController {
    private final DocumentAiNoteService notes;

    public DocumentAiNoteController(DocumentAiNoteService notes) {
        this.notes = notes;
    }

    @PostMapping("/documents/{documentId}/notes")
    public GenerateNotesResponse generate(@PathVariable UUID documentId) {
        return notes.generate(documentId);
    }

    @GetMapping("/documents/{documentId}/notes")
    public DocumentAiNoteResponse latest(@PathVariable UUID documentId) {
        return notes.latest(documentId);
    }

    @GetMapping("/notes/{noteId}/sections")
    public List<DocumentAiNoteSectionResponse> sections(@PathVariable UUID noteId) {
        return notes.sections(noteId);
    }
}
