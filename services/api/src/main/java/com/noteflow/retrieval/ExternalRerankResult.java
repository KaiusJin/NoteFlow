package com.noteflow.retrieval;

import java.util.List;

record ExternalRerankResult(
    List<RetrievalCandidate> candidates,
    String provider,
    boolean applied,
    String error,
    long elapsedMs
) {
}
