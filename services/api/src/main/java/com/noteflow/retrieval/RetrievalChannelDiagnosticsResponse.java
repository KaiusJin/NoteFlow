package com.noteflow.retrieval;

public record RetrievalChannelDiagnosticsResponse(
    String channel,
    boolean available,
    int candidateCount,
    long elapsedMs,
    String error
) {
}
