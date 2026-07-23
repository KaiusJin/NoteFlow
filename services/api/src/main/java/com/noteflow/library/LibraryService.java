package com.noteflow.library;

import com.noteflow.workspace.LocalWorkspaceService;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class LibraryService {
    private final LocalWorkspaceService users;
    private final FolderRepository folders;
    private final NoteRepository notes;

    public LibraryService(LocalWorkspaceService users, FolderRepository folders, NoteRepository notes) {
        this.users = users;
        this.folders = folders;
        this.notes = notes;
    }

    // ----- Folders -------------------------------------------------------

    public List<FolderResponse> listFolders() {
        UUID userId = users.currentUserId();
        return folders.findByUserIdOrderByNameAsc(userId).stream().map(FolderResponse::from).toList();
    }

    @Transactional
    public FolderResponse createFolder(String name, UUID parentId) {
        UUID userId = users.currentUserId();
        String folderName = name == null || name.isBlank() ? "New folder" : name.trim();
        if (parentId != null) {
            requireFolder(parentId, userId);
        }
        Folder folder = new Folder(UUID.randomUUID(), userId, parentId, folderName);
        return FolderResponse.from(folders.save(folder));
    }

    @Transactional
    public FolderResponse renameFolder(UUID folderId, String name) {
        UUID userId = users.currentUserId();
        Folder folder = requireFolder(folderId, userId);
        if (name != null && !name.isBlank()) {
            folder.rename(name.trim());
        }
        return FolderResponse.from(folders.save(folder));
    }

    @Transactional
    public FolderResponse moveFolder(UUID folderId, UUID parentId) {
        UUID userId = users.currentUserId();
        Folder folder = requireFolder(folderId, userId);
        if (parentId != null) {
            requireFolder(parentId, userId);
            if (parentId.equals(folderId) || isDescendant(parentId, folderId)) {
                throw new IllegalArgumentException("Cannot move a folder into itself or its descendant");
            }
        }
        folder.moveTo(parentId);
        return FolderResponse.from(folders.save(folder));
    }

    /** Deletes a folder subtree: descendant folders are removed and their notes moved to Unfiled. */
    @Transactional
    public void deleteFolder(UUID folderId) {
        UUID userId = users.currentUserId();
        Folder folder = requireFolder(folderId, userId);
        Set<UUID> toDelete = new HashSet<>();
        collectSubtree(folder.getId(), toDelete);
        for (UUID id : toDelete) {
            for (Note note : notes.findByFolderId(id)) {
                note.moveTo(null);
                notes.save(note);
            }
        }
        folders.deleteAllById(toDelete);
    }

    // ----- Notes ---------------------------------------------------------

    @Transactional
    public List<NoteResponse> listNotes() {
        UUID userId = users.currentUserId();
        return notes.findByUserIdOrderByUpdatedAtDesc(userId).stream().map(NoteResponse::summary).toList();
    }

    public NoteResponse getNote(UUID noteId) {
        UUID userId = users.currentUserId();
        return NoteResponse.from(requireNote(noteId, userId));
    }

    @Transactional
    public NoteResponse createNote(String title, String markdown, UUID folderId, String sourceKind) {
        UUID userId = users.currentUserId();
        if (folderId != null) {
            requireFolder(folderId, userId);
        }
        String noteTitle = title == null || title.isBlank() ? "Untitled note" : title.trim();
        String kind = normalizeSourceKind(sourceKind);
        Note note = new Note(UUID.randomUUID(), userId, folderId, noteTitle, markdown, kind, null);
        return NoteResponse.from(notes.save(note));
    }

    @Transactional
    public NoteResponse updateNote(UUID noteId, String title, String markdown) {
        UUID userId = users.currentUserId();
        Note note = requireNote(noteId, userId);
        note.update(title, markdown);
        return NoteResponse.from(notes.save(note));
    }

    @Transactional
    public NoteResponse moveNote(UUID noteId, UUID folderId) {
        UUID userId = users.currentUserId();
        Note note = requireNote(noteId, userId);
        if (folderId != null) {
            requireFolder(folderId, userId);
        }
        note.moveTo(folderId);
        return NoteResponse.from(notes.save(note));
    }

    @Transactional
    public NoteResponse renameNote(UUID noteId, String title) {
        UUID userId = users.currentUserId();
        Note note = requireNote(noteId, userId);
        if (title != null && !title.isBlank()) {
            note.rename(title.trim());
        }
        return NoteResponse.from(notes.save(note));
    }

    @Transactional
    public void deleteNote(UUID noteId) {
        UUID userId = users.currentUserId();
        Note note = requireNote(noteId, userId);
        notes.delete(note);
    }

    /** Imports a .md/.txt file body as a new note. */
    @Transactional
    public NoteResponse importNote(String fileName, String content, UUID folderId) {
        String title = fileName == null || fileName.isBlank()
            ? "Imported note"
            : fileName.replaceAll("\\.(md|markdown|txt)$", "").trim();
        return createNote(title.isBlank() ? "Imported note" : title, content, folderId, "IMPORT");
    }

    // ----- Helpers -------------------------------------------------------

    private Folder requireFolder(UUID folderId, UUID userId) {
        return folders.findByIdAndUserId(folderId, userId)
            .orElseThrow(() -> new IllegalArgumentException("Folder not found"));
    }

    private Note requireNote(UUID noteId, UUID userId) {
        return notes.findByIdAndUserId(noteId, userId)
            .orElseThrow(() -> new IllegalArgumentException("Note not found"));
    }

    private String normalizeSourceKind(String sourceKind) {
        String kind = sourceKind == null || sourceKind.isBlank() ? "BLANK" : sourceKind.trim().toUpperCase();
        return switch (kind) {
            case "RAW", "PDF", "PDF_MARKDOWN", "RAW_MARKDOWN" -> "RAW";
            case "AI_NOTE", "AI", "AI_NOTES" -> "AI_NOTE";
            case "IMPORT", "IMPORTED" -> "IMPORT";
            case "BLANK", "NOTE", "MY_NOTE" -> "BLANK";
            default -> throw new IllegalArgumentException("sourceKind must be RAW, AI_NOTE, IMPORT, or BLANK");
        };
    }

    private void collectSubtree(UUID folderId, Set<UUID> acc) {
        if (!acc.add(folderId)) return;
        for (Folder child : folders.findByParentId(folderId)) {
            collectSubtree(child.getId(), acc);
        }
    }

    private boolean isDescendant(UUID candidate, UUID ancestor) {
        Set<UUID> subtree = new HashSet<>();
        collectSubtree(ancestor, subtree);
        return subtree.contains(candidate);
    }
}
