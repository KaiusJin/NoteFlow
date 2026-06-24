package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class EvidenceEvaluatorTest {
    private final EvidenceEvaluator evaluator = new EvidenceEvaluator(0.60, 0.52);

    @Test
    void assignsDeterministicEvidenceStatuses() {
        assertThat(evaluator.evaluate(List.of())).isEqualTo(EvidenceStatus.NO_RESULTS);
        assertThat(evaluator.evaluate(List.of(item("AI_NOTE", 0.61))))
            .isEqualTo(EvidenceStatus.WEAK);
        assertThat(evaluator.evaluate(List.of(item("PDF", 0.61))))
            .isEqualTo(EvidenceStatus.SUFFICIENT);
        assertThat(evaluator.evaluate(List.of(item("PDF", 0.55))))
            .isEqualTo(EvidenceStatus.WEAK);
        assertThat(evaluator.evaluate(List.of(item("PDF", 0.49))))
            .isEqualTo(EvidenceStatus.INSUFFICIENT);
    }

    private RetrievalItemResponse item(String domain, double score) {
        return new RetrievalItemResponse(
            "S1",
            domain,
            "PDF".equals(domain) ? "DOCUMENT_CHUNK" : "AI_NOTE_SECTION",
            UUID.randomUUID(),
            "Document",
            1,
            1,
            List.of(UUID.randomUUID()),
            "Title",
            "Useful evidence content.",
            10,
            score,
            "PDF".equals(domain) ? score : null,
            null,
            null,
            1.0,
            List.of("VECTOR"),
            false
        );
    }
}
