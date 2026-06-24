package com.noteflow.retrieval;

public record RetrievalDiagnosticsResponse(
    int vectorCandidateCount,
    int lexicalCandidateCount,
    int exactCandidateCount,
    int filteredCandidateCount,
    int fusedCandidateCount,
    int deduplicatedCandidateCount,
    int rerankedCandidateCount,
    int contextItemCount,
    java.util.List<RetrievalChannelDiagnosticsResponse> channels,
    String externalRerankerProvider,
    boolean externalRerankerApplied,
    String externalRerankerError,
    long externalRerankerMs,
    boolean hydeTriggered,
    boolean hydeGenerated,
    String hydeProvider,
    String hydeError,
    long hydeMs,
    long recallMs,
    long fusionAndRerankMs,
    long contextBuildMs,
    long totalMs
) {
}
