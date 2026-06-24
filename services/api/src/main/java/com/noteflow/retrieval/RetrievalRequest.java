package com.noteflow.retrieval;

import com.noteflow.search.SearchMode;
import java.util.List;
import java.util.UUID;

public record RetrievalRequest(
    String query,
    Integer topK,
    SearchMode mode,
    List<UUID> pdfDocumentIds,
    List<UUID> aiNoteDocumentIds,
    Integer maxContextTokens
) {
}
