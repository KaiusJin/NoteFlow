package com.noteflow.retrieval;

import java.util.UUID;
import java.util.List;

record RetrievalCandidate(
    String sourceDomain,
    String sourceObjectType,
    UUID sourceObjectId,
    UUID documentId,
    String documentTitle,
    Integer pageStart,
    Integer pageEnd,
    String title,
    String content,
    Integer chunkIndex,
    Integer tokenCount,
    double score,
    Double vectorScore,
    Double lexicalScore,
    Double exactScore,
    double fusionScore,
    List<String> matchedChannels
) {
    RetrievalCandidate(
        String sourceDomain,
        String sourceObjectType,
        UUID sourceObjectId,
        UUID documentId,
        String documentTitle,
        Integer pageStart,
        Integer pageEnd,
        String title,
        String content,
        Integer chunkIndex,
        Integer tokenCount,
        double score
    ) {
        this(
            sourceDomain,
            sourceObjectType,
            sourceObjectId,
            documentId,
            documentTitle,
            pageStart,
            pageEnd,
            title,
            content,
            chunkIndex,
            tokenCount,
            score,
            score,
            null,
            null,
            0,
            List.of("VECTOR")
        );
    }

    RetrievalCandidate withScores(
        Double newVectorScore,
        Double newLexicalScore,
        Double newExactScore,
        double newFusionScore,
        List<String> channels
    ) {
        double evidenceScore = maxScore(newVectorScore, newLexicalScore, newExactScore);
        return new RetrievalCandidate(
            sourceDomain,
            sourceObjectType,
            sourceObjectId,
            documentId,
            documentTitle,
            pageStart,
            pageEnd,
            title,
            content,
            chunkIndex,
            tokenCount,
            evidenceScore,
            newVectorScore,
            newLexicalScore,
            newExactScore,
            newFusionScore,
            List.copyOf(channels)
        );
    }

    private static double maxScore(Double vector, Double lexical, Double exact) {
        double maximum = 0;
        if (vector != null) {
            maximum = Math.max(maximum, vector);
        }
        if (lexical != null) {
            maximum = Math.max(maximum, lexical);
        }
        if (exact != null) {
            maximum = Math.max(maximum, exact);
        }
        return maximum;
    }
}
