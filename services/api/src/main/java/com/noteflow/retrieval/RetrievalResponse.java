package com.noteflow.retrieval;

import com.noteflow.search.SearchMode;
import java.util.List;

public record RetrievalResponse(
    String query,
    SearchMode mode,
    EvidenceStatus evidenceStatus,
    int contextTokenCount,
    List<RetrievalItemResponse> items,
    RetrievalDiagnosticsResponse diagnostics
) {
}
