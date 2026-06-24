package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CandidateDeduplicatorTest {
    private static final UUID DOCUMENT_ID = UUID.randomUUID();
    private final CandidateDeduplicator deduplicator = new CandidateDeduplicator(0.88);

    @Test
    void removesNearDuplicateContentWithinSameDocument() {
        RetrievalCandidate first = candidate(
            UUID.randomUUID(),
            "Taylor inequality bounds the remainder using the next derivative and a factorial.",
            0.81
        );
        RetrievalCandidate duplicate = candidate(
            UUID.randomUUID(),
            "Taylor inequality bounds the remainder using the next derivative and a factorial.",
            0.79
        );
        RetrievalCandidate different = candidate(
            UUID.randomUUID(),
            "The alternating series test requires decreasing terms that approach zero.",
            0.70
        );

        assertThat(deduplicator.deduplicate(List.of(first, duplicate, different)))
            .containsExactly(first, different);
    }

    @Test
    void keepsSameTextFromDifferentDocuments() {
        String content = "A probability mass function assigns probabilities to discrete outcomes.";
        RetrievalCandidate first = candidate(UUID.randomUUID(), content, 0.75);
        RetrievalCandidate second = new RetrievalCandidate(
            "AI_NOTE",
            "AI_NOTE_SECTION",
            UUID.randomUUID(),
            UUID.randomUUID(),
            "Another document",
            2,
            2,
            "PMF",
            content,
            null,
            null,
            0.73
        );

        assertThat(deduplicator.deduplicate(List.of(first, second))).containsExactly(first, second);
    }

    private RetrievalCandidate candidate(UUID sourceId, String content, double score) {
        return new RetrievalCandidate(
            "PDF",
            "DOCUMENT_CHUNK",
            sourceId,
            DOCUMENT_ID,
            "MATH138",
            1,
            2,
            "Taylor Series",
            content,
            3,
            30,
            score
        );
    }
}
