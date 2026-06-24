package com.noteflow.retrieval;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class QueryAwareReranker {
    private static final Set<String> STOP_WORDS = Set.of(
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "in", "is", "it", "of", "on", "or", "that", "the", "to",
        "what", "when", "where", "which", "why", "with"
    );

    private final double vectorWeight;
    private final double lexicalWeight;
    private final double minimumQueryCoverage;
    private final double minimumFusionScore;

    QueryAwareReranker(
        @Value("${noteflow.retrieval.rerank-vector-weight:0.75}") double vectorWeight,
        @Value("${noteflow.retrieval.rerank-lexical-weight:0.25}") double lexicalWeight,
        @Value("${noteflow.retrieval.rerank-minimum-query-coverage:0.20}") double minimumQueryCoverage,
        @Value("${noteflow.retrieval.rerank-minimum-fusion-score:0.45}") double minimumFusionScore
    ) {
        this.vectorWeight = vectorWeight;
        this.lexicalWeight = lexicalWeight;
        this.minimumQueryCoverage = minimumQueryCoverage;
        this.minimumFusionScore = minimumFusionScore;
    }

    List<RetrievalCandidate> rerank(String query, List<RetrievalCandidate> candidates, int preferredLimit) {
        Set<String> queryTokens = tokens(query);
        if (queryTokens.isEmpty()) {
            return candidates;
        }
        List<ScoredCandidate> scored = new ArrayList<>();
        for (int index = 0; index < candidates.size(); index++) {
            RetrievalCandidate candidate = candidates.get(index);
            double titleCoverage = coverage(queryTokens, tokens(candidate.title()));
            double contentCoverage = coverage(queryTokens, tokens(candidate.content()));
            double lexicalCoverage = titleCoverage * 0.65 + contentCoverage * 0.35;
            double retrievalBase = candidate.fusionScore() > 0
                ? candidate.fusionScore()
                : candidate.score();
            double combinedScore = vectorWeight * retrievalBase + lexicalWeight * lexicalCoverage;
            scored.add(new ScoredCandidate(candidate, combinedScore, lexicalCoverage, index));
        }
        List<ScoredCandidate> rankedScores = scored.stream()
            .sorted(
                Comparator.comparingDouble(ScoredCandidate::combinedScore).reversed()
                    .thenComparingInt(ScoredCandidate::originalIndex)
            )
            .toList();
        List<RetrievalCandidate> ranked = new ArrayList<>();
        for (int index = 0; index < rankedScores.size(); index++) {
            ScoredCandidate scoredCandidate = rankedScores.get(index);
            RetrievalCandidate candidate = scoredCandidate.candidate();
            boolean multiChannel = candidate.matchedChannels().size() > 1;
            boolean strongFusion = candidate.fusionScore() >= minimumFusionScore;
            boolean directQueryCoverage = scoredCandidate.lexicalCoverage() >= minimumQueryCoverage;
            if (index < 2 || multiChannel || strongFusion || directQueryCoverage) {
                ranked.add(candidate);
            }
        }
        ensurePdfEvidence(ranked, preferredLimit);
        return ranked;
    }

    private void ensurePdfEvidence(List<RetrievalCandidate> ranked, int preferredLimit) {
        int inspected = Math.min(preferredLimit, ranked.size());
        boolean hasPdf = ranked.subList(0, inspected).stream()
            .anyMatch(candidate -> "PDF".equals(candidate.sourceDomain()));
        if (hasPdf) {
            return;
        }
        for (int index = inspected; index < ranked.size(); index++) {
            if ("PDF".equals(ranked.get(index).sourceDomain())) {
                RetrievalCandidate pdf = ranked.remove(index);
                int insertionIndex = Math.min(Math.max(0, preferredLimit - 1), Math.min(3, ranked.size()));
                ranked.add(insertionIndex, pdf);
                return;
            }
        }
    }

    private double coverage(Set<String> queryTokens, Set<String> candidateTokens) {
        if (queryTokens.isEmpty()) {
            return 0;
        }
        Set<String> matching = new HashSet<>(queryTokens);
        matching.retainAll(candidateTokens);
        return (double) matching.size() / queryTokens.size();
    }

    private Set<String> tokens(String value) {
        if (value == null || value.isBlank()) {
            return Set.of();
        }
        String normalized = value.toLowerCase(Locale.ROOT)
            .replaceAll("[^\\p{L}\\p{N}_]+", " ")
            .replaceAll("\\s+", " ")
            .strip();
        if (normalized.isBlank()) {
            return Set.of();
        }
        Set<String> result = new HashSet<>();
        for (String token : normalized.split(" ")) {
            if (token.length() > 1 && !STOP_WORDS.contains(token)) {
                result.add(token);
            }
        }
        return result;
    }

    private record ScoredCandidate(
        RetrievalCandidate candidate,
        double combinedScore,
        double lexicalCoverage,
        int originalIndex
    ) {
    }
}
