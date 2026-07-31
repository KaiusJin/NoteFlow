package com.noteflow.library;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.noteflow.workspace.LocalWorkspaceService;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class LibraryServiceConcurrencyTest {
    private final UUID userId = UUID.randomUUID();
    private final UUID noteId = UUID.randomUUID();
    private final LocalWorkspaceService workspace = mock(LocalWorkspaceService.class);
    private final FolderRepository folders = mock(FolderRepository.class);
    private final NoteRepository notes = mock(NoteRepository.class);
    private final LibraryService service = new LibraryService(workspace, folders, notes);

    @Test
    void updateRequiresExpectedVersion() {
        when(workspace.currentUserId()).thenReturn(userId);
        assertThrows(
            IllegalArgumentException.class,
            () -> service.updateNote(noteId, "Title", "Body", null)
        );
    }

    @Test
    void staleUpdateReturnsConflictInsteadOfOverwriting() {
        Instant expected = Instant.parse("2026-01-01T00:00:00Z");
        when(workspace.currentUserId()).thenReturn(userId);
        when(notes.findByIdAndUserId(noteId, userId)).thenReturn(Optional.of(mock(Note.class)));
        when(notes.updateContentIfUnchanged(
            org.mockito.ArgumentMatchers.eq(noteId),
            org.mockito.ArgumentMatchers.eq(userId),
            org.mockito.ArgumentMatchers.eq("Title"),
            org.mockito.ArgumentMatchers.eq("Body"),
            org.mockito.ArgumentMatchers.eq(expected),
            org.mockito.ArgumentMatchers.any(Instant.class)
        )).thenReturn(0);

        assertThrows(
            ConcurrentNoteEditException.class,
            () -> service.updateNote(noteId, "Title", "Body", expected)
        );
    }
}
