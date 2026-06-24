package com.noteflow.retrieval;

import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class EvidenceEvaluator {
    private final double strongScore;
    private final double weakScore;

    EvidenceEvaluator(
        @Value("${noteflow.retrieval.strong-score:0.60}") double strongScore,
        @Value("${noteflow.retrieval.weak-score:0.52}") double weakScore
    ) {
        this.strongScore = strongScore;
        this.weakScore = weakScore;
    }

    EvidenceStatus evaluate(List<RetrievalItemResponse> items) {
        if (items.isEmpty()) {
            return EvidenceStatus.NO_RESULTS;
        }
        RetrievalItemResponse first = items.get(0);
        boolean hasPdf = items.stream().anyMatch(item -> "PDF".equals(item.sourceDomain()));
        if (first.score() >= strongScore && (hasPdf || items.size() >= 2)) {
            return EvidenceStatus.SUFFICIENT;
        }
        if (first.score() >= weakScore) {
            return EvidenceStatus.WEAK;
        }
        return EvidenceStatus.INSUFFICIENT;
    }
}
