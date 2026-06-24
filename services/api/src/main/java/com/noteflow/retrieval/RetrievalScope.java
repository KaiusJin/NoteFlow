package com.noteflow.retrieval;

import java.util.List;
import java.util.UUID;

record RetrievalScope(List<UUID> pdfDocumentIds, List<UUID> aiNoteDocumentIds) {
    boolean isEmpty() {
        return pdfDocumentIds.isEmpty() && aiNoteDocumentIds.isEmpty();
    }
}
