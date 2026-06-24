package com.noteflow.retrieval;

record HydeExpansionResult(
    boolean triggered,
    boolean generated,
    String provider,
    String hypotheticalDocument,
    String error,
    long elapsedMs
) {
}
