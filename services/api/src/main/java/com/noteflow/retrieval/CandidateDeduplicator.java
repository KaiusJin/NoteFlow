package com.noteflow.retrieval;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class CandidateDeduplicator {
    private final double nearDuplicateThreshold;

    CandidateDeduplicator(
        @Value("${noteflow.retrieval.near-duplicate-threshold:0.88}") double nearDuplicateThreshold
    ) {
        this.nearDuplicateThreshold = nearDuplicateThreshold;
    }

    List<RetrievalCandidate> deduplicate(List<RetrievalCandidate> candidates) {
        Map<String, RetrievalCandidate> uniqueSources = new LinkedHashMap<>();
        for (RetrievalCandidate candidate : candidates) {
            String key = candidate.sourceDomain() + ":" + candidate.sourceObjectType() + ":" + candidate.sourceObjectId();
            uniqueSources.putIfAbsent(key, candidate);
        }

        List<RetrievalCandidate> result = new ArrayList<>();
        for (RetrievalCandidate candidate : uniqueSources.values()) {
            boolean duplicate = result.stream().anyMatch(existing -> nearDuplicate(existing, candidate));
            if (!duplicate) {
                result.add(candidate);
            }
        }
        return result;
    }

    private boolean nearDuplicate(RetrievalCandidate left, RetrievalCandidate right) {
        if (!left.documentId().equals(right.documentId())) {
            return false;
        }
        String leftText = normalize(left.content());
        String rightText = normalize(right.content());
        if (leftText.equals(rightText)) {
            return true;
        }
        Set<String> leftTokens = tokens(leftText);
        Set<String> rightTokens = tokens(rightText);
        if (leftTokens.isEmpty() || rightTokens.isEmpty()) {
            return false;
        }
        Set<String> intersection = new HashSet<>(leftTokens);
        intersection.retainAll(rightTokens);
        Set<String> union = new HashSet<>(leftTokens);
        union.addAll(rightTokens);
        return (double) intersection.size() / union.size() >= nearDuplicateThreshold;
    }

    private String normalize(String text) {
        return text.toLowerCase(Locale.ROOT)
            .replaceAll("[^\\p{L}\\p{N}_]+", " ")
            .replaceAll("\\s+", " ")
            .strip();
    }

    private Set<String> tokens(String normalized) {
        return new HashSet<>(List.of(normalized.split(" ")));
    }
}
