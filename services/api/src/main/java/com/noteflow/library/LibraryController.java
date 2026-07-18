package com.noteflow.library;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
public class LibraryController {
    private final LibraryService library;

    public LibraryController(LibraryService library) {
        this.library = library;
    }

    // ----- Folders -------------------------------------------------------

    @GetMapping("/folders")
    public List<FolderResponse> listFolders() {
        return library.listFolders();
    }

    @PostMapping("/folders")
    public FolderResponse createFolder(@RequestBody FolderRequest request) {
        return library.createFolder(request.name(), request.parentId());
    }

    @PutMapping("/folders/{folderId}")
    public FolderResponse updateFolder(@PathVariable UUID folderId, @RequestBody FolderUpdateRequest request) {
        if (request.move()) {
            return library.moveFolder(folderId, request.parentId());
        }
        return library.renameFolder(folderId, request.name());
    }

    @DeleteMapping("/folders/{folderId}")
    public ResponseEntity<Void> deleteFolder(@PathVariable UUID folderId) {
        library.deleteFolder(folderId);
        return ResponseEntity.noContent().build();
    }

    // ----- Notes ---------------------------------------------------------

    @GetMapping("/notes")
    public List<NoteResponse> listNotes() {
        return library.listNotes();
    }

    @GetMapping("/notes/{noteId}")
    public NoteResponse getNote(@PathVariable UUID noteId) {
        return library.getNote(noteId);
    }

    @PostMapping("/notes")
    public NoteResponse createNote(@RequestBody NoteCreateRequest request) {
        return library.createNote(request.title(), request.markdown(), request.folderId(), request.sourceKind());
    }

    @PutMapping("/notes/{noteId}")
    public NoteResponse updateNote(@PathVariable UUID noteId, @RequestBody NoteUpdateRequest request) {
        if (request.move()) {
            return library.moveNote(noteId, request.folderId());
        }
        if (request.title() != null && request.markdown() == null) {
            return library.renameNote(noteId, request.title());
        }
        return library.updateNote(noteId, request.title(), request.markdown());
    }

    @DeleteMapping("/notes/{noteId}")
    public ResponseEntity<Void> deleteNote(@PathVariable UUID noteId) {
        library.deleteNote(noteId);
        return ResponseEntity.noContent().build();
    }

    @PostMapping(value = "/notes/import", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public NoteResponse importNote(@RequestPart("file") MultipartFile file,
            @RequestParam(value = "folderId", required = false) UUID folderId) {
        try {
            String content = new String(file.getBytes(), StandardCharsets.UTF_8);
            return library.importNote(file.getOriginalFilename(), content, folderId);
        } catch (java.io.IOException ex) {
            throw new IllegalArgumentException("Could not read uploaded file");
        }
    }

    @GetMapping("/notes/{noteId}/export")
    public ResponseEntity<byte[]> exportNote(@PathVariable UUID noteId) {
        NoteResponse note = library.getNote(noteId);
        String safeName = (note.title() == null ? "note" : note.title()).replaceAll("[\\\\/:*?\"<>|]", "_");
        byte[] body = (note.markdown() == null ? "" : note.markdown()).getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.ok()
            .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + safeName + ".md\"")
            .contentType(MediaType.TEXT_MARKDOWN)
            .body(body);
    }

    public record FolderRequest(String name, UUID parentId) {
    }

    public record FolderUpdateRequest(String name, UUID parentId, boolean move) {
    }

    public record NoteCreateRequest(String title, String markdown, UUID folderId, String sourceKind) {
    }

    public record NoteUpdateRequest(String title, String markdown, UUID folderId, boolean move) {
    }
}
