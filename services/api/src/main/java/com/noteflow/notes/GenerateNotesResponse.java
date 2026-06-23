package com.noteflow.notes;

import java.util.UUID;

public record GenerateNotesResponse(
    UUID noteId,
    UUID taskId,
    String status
) {
}
