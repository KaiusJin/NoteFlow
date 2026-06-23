package com.noteflow.search;

import java.util.List;
import java.util.UUID;

public record SearchRequest(
    String query,
    Integer topK,
    SearchMode mode,
    List<UUID> pdfDocumentIds,
    List<UUID> aiNoteDocumentIds
) {
}
