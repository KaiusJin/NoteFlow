package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class QueryAwareRerankerTest {
    private final QueryAwareReranker reranker = new QueryAwareReranker(0.75, 0.25, 0.20, 0.45);

    @Test
    void promotesCandidateWithDirectQueryTermCoverage() {
        RetrievalCandidate broad = candidate(
            "Geometric Distribution: Memorylessness",
            "The geometric distribution has a memoryless property.",
            0.72
        );
        RetrievalCandidate direct = candidate(
            "Geometric Distribution: Probability Mass Function",
            "The probability mass function is (1-p)^(x-1)p.",
            0.70
        );

        List<RetrievalCandidate> result = reranker.rerank(
            "probability mass function of geometric distribution",
            List.of(broad, direct),
            2
        );

        assertThat(result).containsExactly(direct, broad);
        assertThat(result.get(0).score()).isEqualTo(0.70);
    }

    @Test
    void preservesVectorOrderWhenLexicalCoverageIsEqual() {
        RetrievalCandidate first = candidate("Taylor theorem", "remainder bound", 0.80);
        RetrievalCandidate second = candidate("Taylor theorem", "remainder bound", 0.70);

        assertThat(reranker.rerank("Taylor remainder", List.of(first, second), 2))
            .containsExactly(first, second);
    }

    @Test
    void retainsOneQualifiedPdfCandidateWithinPreferredResults() {
        RetrievalCandidate noteOne = candidate("PMF definition", "probability mass function", 0.82);
        RetrievalCandidate noteTwo = candidate("Geometric setup", "first success", 0.78);
        RetrievalCandidate noteThree = candidate("Geometric CDF", "cumulative probability", 0.76);
        RetrievalCandidate pdf = new RetrievalCandidate(
            "PDF",
            "DOCUMENT_CHUNK",
            UUID.randomUUID(),
            UUID.randomUUID(),
            "Document",
            1,
            2,
            "Lecture",
            "Original PDF probability mass function.",
            0,
            100,
            0.65
        );

        List<RetrievalCandidate> result = reranker.rerank(
            "probability mass function",
            List.of(noteOne, noteTwo, noteThree, pdf),
            3
        );

        assertThat(result.subList(0, 3))
            .anyMatch(candidate -> "PDF".equals(candidate.sourceDomain()));
    }

    @Test
    void doesNotPadExactResultsWithWeakUnrelatedVectorCandidates() {
        RetrievalCandidate exact = fusedCandidate(
            "Pitfall: list_cp_bad shallow copy",
            "list_cp_bad creates a shallow copy",
            1.0,
            List.of("VECTOR", "LEXICAL", "EXACT")
        );
        RetrievalCandidate supporting = fusedCandidate(
            "Deep Copy",
            "A deep copy allocates separate nodes",
            0.30,
            List.of("VECTOR")
        );
        RetrievalCandidate unrelated = fusedCandidate(
            "Stack implementation",
            "A stack stores generic pointers",
            0.28,
            List.of("VECTOR")
        );

        List<RetrievalCandidate> result = reranker.rerank(
            "list_cp_bad shallow copy",
            List.of(exact, supporting, unrelated),
            6
        );

        assertThat(result).contains(exact, supporting).doesNotContain(unrelated);
    }

    private RetrievalCandidate candidate(String title, String content, double score) {
        return new RetrievalCandidate(
            "AI_NOTE",
            "AI_NOTE_SECTION",
            UUID.randomUUID(),
            UUID.randomUUID(),
            "Document",
            1,
            2,
            title,
            content,
            null,
            null,
            score
        );
    }

    private RetrievalCandidate fusedCandidate(
        String title,
        String content,
        double fusionScore,
        List<String> channels
    ) {
        RetrievalCandidate base = candidate(title, content, 0.70);
        return base.withScores(0.70, null, channels.contains("EXACT") ? 1.0 : null, fusionScore, channels);
    }
}
