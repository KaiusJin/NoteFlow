package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CandidateQualityFilterTest {
    private final CandidateQualityFilter filter = new CandidateQualityFilter(0.48, 0.01, 0.25, 20);

    @Test
    void removesLowScoreBlankAndVeryShortCandidates() {
        RetrievalCandidate useful = candidate(
            "Taylor's inequality bounds the remainder of a Taylor polynomial.",
            0.72
        );

        List<RetrievalCandidate> result = filter.filter(List.of(
            useful,
            candidate("The image region is completely blank, containing no visible text.", 0.70),
            candidate("short", 0.80),
            candidate("Relevant-looking content that falls below the score floor.", 0.40)
        ));

        assertThat(result).containsExactly(useful);
    }

    private RetrievalCandidate candidate(String content, double score) {
        return new RetrievalCandidate(
            "PDF",
            "DOCUMENT_CHUNK",
            UUID.randomUUID(),
            UUID.randomUUID(),
            "Document",
            1,
            1,
            "Section",
            content,
            0,
            20,
            score
        );
    }
}
