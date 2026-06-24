package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.http.HttpClient;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ExternalSemanticRerankerTest {
    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void disabledProviderPreservesDeterministicOrder() {
        ExternalSemanticReranker reranker = new ExternalSemanticReranker(
            objectMapper,
            HttpClient.newHttpClient(),
            "disabled",
            "",
            "gemini-2.5-flash",
            20,
            12
        );
        List<RetrievalCandidate> candidates = List.of(candidate("first"), candidate("second"));

        ExternalRerankResult result = reranker.rerank("query", candidates);

        assertThat(result.candidates()).containsExactlyElementsOf(candidates);
        assertThat(result.applied()).isFalse();
        assertThat(result.error()).isNull();
    }

    @Test
    void appliesOnlyKnownCandidateIdsAndKeepsOmittedCandidatesLast() throws Exception {
        ExternalSemanticReranker reranker = new ExternalSemanticReranker(
            objectMapper,
            HttpClient.newHttpClient(),
            "disabled",
            "",
            "gemini-2.5-flash",
            20,
            12
        );
        RetrievalCandidate first = candidate("first");
        RetrievalCandidate second = candidate("second");
        RetrievalCandidate third = candidate("third");

        List<RetrievalCandidate> result = reranker.applyRanking(
            List.of(first, second, third),
            """
            [
              {"id":"C2","score":0.95},
              {"id":"C1","score":0.40},
              {"id":"invented","score":1.0}
            ]
            """
        );

        assertThat(result).containsExactly(second, first, third);
    }

    @Test
    void rejectsResponseThatDoesNotReferenceAnyInputCandidate() {
        ExternalSemanticReranker reranker = new ExternalSemanticReranker(
            objectMapper,
            HttpClient.newHttpClient(),
            "disabled",
            "",
            "gemini-2.5-flash",
            20,
            12
        );

        assertThatThrownBy(() -> reranker.applyRanking(
            List.of(candidate("first")),
            "[{\"id\":\"invented\",\"score\":1.0}]"
        )).isInstanceOf(IllegalArgumentException.class);
    }

    private RetrievalCandidate candidate(String title) {
        return new RetrievalCandidate(
            "PDF",
            "DOCUMENT_CHUNK",
            UUID.randomUUID(),
            UUID.randomUUID(),
            "Document",
            1,
            1,
            title,
            title + " evidence",
            0,
            10,
            0.7
        );
    }
}
