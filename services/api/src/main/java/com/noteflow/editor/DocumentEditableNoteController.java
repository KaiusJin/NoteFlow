package com.noteflow.editor;

import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DocumentEditableNoteController {
    private final DocumentEditableNoteService editableNotes;

    public DocumentEditableNoteController(DocumentEditableNoteService editableNotes) {
        this.editableNotes = editableNotes;
    }

    @GetMapping("/documents/{documentId}/editable-note")
    public ResponseEntity<DocumentEditableNoteResponse> latest(@PathVariable UUID documentId) {
        return editableNotes.latest(documentId)
            .map(ResponseEntity::ok)
            .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping("/documents/{documentId}/editable-note")
    public DocumentEditableNoteResponse initialize(@PathVariable UUID documentId,
            @RequestBody(required = false) InitEditableNoteRequest request) {
        return editableNotes.initialize(documentId, request == null ? null : request.source());
    }

    @PutMapping("/documents/{documentId}/editable-note")
    public DocumentEditableNoteResponse save(@PathVariable UUID documentId,
            @RequestBody SaveEditableNoteRequest request) {
        return editableNotes.save(documentId, request.title(), request.markdown());
    }

    public record InitEditableNoteRequest(String source) {
    }

    public record SaveEditableNoteRequest(String title, String markdown) {
    }
}
