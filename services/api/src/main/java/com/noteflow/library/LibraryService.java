package com.noteflow.library;

import com.noteflow.workspace.LocalWorkspaceService;
import com.noteflow.common.CursorPage;
import com.noteflow.common.OpaqueCursor;
import java.time.Instant;
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
        return listNotes(100, null).items();
    }

    @Transactional
    public CursorPage<NoteResponse> listNotes(int requestedLimit, String rawCursor) {
        UUID userId = users.currentUserId();
        int limit = Math.max(1, Math.min(200, requestedLimit));
        OpaqueCursor cursor = OpaqueCursor.decode(rawCursor);
        List<Note> fetched = cursor == null
            ? notes.findCursorPage(userId, limit + 1)
            : notes.findCursorPageAfter(userId, cursor.timestamp(), cursor.id(), limit + 1);
        boolean hasNext = fetched.size() > limit;
        List<Note> page = hasNext ? fetched.subList(0, limit) : fetched;
        Note last = hasNext ? page.getLast() : null;
        return new CursorPage<>(
            page.stream().map(NoteResponse::summary).toList(),
            last == null ? null : new OpaqueCursor(last.getUpdatedAt(), last.getId()).encode()
        );
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
    public NoteResponse updateNote(UUID noteId, String title, String markdown, Instant expectedUpdatedAt) {
        UUID userId = users.currentUserId();
        requireExpectedVersion(expectedUpdatedAt);
        requireNote(noteId, userId);
        int updated = notes.updateContentIfUnchanged(
            noteId, userId, title, markdown == null ? "" : markdown, expectedUpdatedAt, Instant.now()
        );
        if (updated != 1) {
            throw new ConcurrentNoteEditException();
        }
        return NoteResponse.from(requireNote(noteId, userId));
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
    public NoteResponse renameNote(UUID noteId, String title, Instant expectedUpdatedAt) {
        UUID userId = users.currentUserId();
        requireExpectedVersion(expectedUpdatedAt);
        requireNote(noteId, userId);
        if (title == null || title.isBlank()) {
            throw new IllegalArgumentException("Note title is required");
        }
        int updated = notes.renameIfUnchanged(noteId, userId, title.trim(), expectedUpdatedAt, Instant.now());
        if (updated != 1) {
            throw new ConcurrentNoteEditException();
        }
        return NoteResponse.from(requireNote(noteId, userId));
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

    private void requireExpectedVersion(Instant expectedUpdatedAt) {
        if (expectedUpdatedAt == null) {
            throw new IllegalArgumentException("expectedUpdatedAt is required");
        }
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
