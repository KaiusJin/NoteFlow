package com.noteflow.retrieval;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class ReciprocalRankFusion {
    private final int rankConstant;
    private final double vectorWeight;
    private final double lexicalWeight;
    private final double exactWeight;

    ReciprocalRankFusion(
        @Value("${noteflow.retrieval.rrf-rank-constant:60}") int rankConstant,
        @Value("${noteflow.retrieval.rrf-vector-weight:1.0}") double vectorWeight,
        @Value("${noteflow.retrieval.rrf-lexical-weight:1.0}") double lexicalWeight,
        @Value("${noteflow.retrieval.rrf-exact-weight:1.2}") double exactWeight
    ) {
        this.rankConstant = rankConstant;
        this.vectorWeight = vectorWeight;
        this.lexicalWeight = lexicalWeight;
        this.exactWeight = exactWeight;
    }

    List<RetrievalCandidate> fuse(List<ChannelRecallResult> channelResults) {
        Map<CandidateKey, Aggregate> aggregates = new LinkedHashMap<>();
        for (ChannelRecallResult result : channelResults) {
            if (!result.available()) {
                continue;
            }
            double weight = weight(result.channel());
            for (int index = 0; index < result.candidates().size(); index++) {
                RetrievalCandidate candidate = result.candidates().get(index);
                CandidateKey key = new CandidateKey(
                    candidate.sourceDomain(),
                    candidate.sourceObjectType(),
                    candidate.sourceObjectId()
                );
                Aggregate aggregate = aggregates.computeIfAbsent(key, ignored -> new Aggregate(candidate));
                aggregate.add(result.channel(), candidate.score(), weight / (rankConstant + index + 1.0));
            }
        }
        if (aggregates.isEmpty()) {
            return List.of();
        }

        double maximumFusionScore = aggregates.values().stream()
            .mapToDouble(Aggregate::fusionScore)
            .max()
            .orElse(1.0);
        List<RetrievalCandidate> fused = new ArrayList<>();
        for (Aggregate aggregate : aggregates.values()) {
            double normalizedFusion = maximumFusionScore == 0
                ? 0
                : aggregate.fusionScore() / maximumFusionScore;
            fused.add(aggregate.toCandidate(normalizedFusion));
        }
        return fused.stream()
            .sorted(
                Comparator.comparingDouble(RetrievalCandidate::fusionScore).reversed()
                    .thenComparing(Comparator.comparingDouble(RetrievalCandidate::score).reversed())
            )
            .toList();
    }

    private double weight(RetrievalChannel channel) {
        return switch (channel) {
            case VECTOR -> vectorWeight;
            case LEXICAL -> lexicalWeight;
            case EXACT -> exactWeight;
        };
    }

    private record CandidateKey(
        String sourceDomain,
        String sourceObjectType,
        java.util.UUID sourceObjectId
    ) {
    }

    private static final class Aggregate {
        private final RetrievalCandidate base;
        private final List<String> channels = new ArrayList<>();
        private Double vectorScore;
        private Double lexicalScore;
        private Double exactScore;
        private double fusionScore;

        private Aggregate(RetrievalCandidate base) {
            this.base = base;
        }

        void add(RetrievalChannel channel, double score, double contribution) {
            fusionScore += contribution;
            if (!channels.contains(channel.name())) {
                channels.add(channel.name());
            }
            switch (channel) {
                case VECTOR -> vectorScore = maximum(vectorScore, score);
                case LEXICAL -> lexicalScore = maximum(lexicalScore, score);
                case EXACT -> exactScore = maximum(exactScore, score);
            }
        }

        double fusionScore() {
            return fusionScore;
        }

        RetrievalCandidate toCandidate(double normalizedFusionScore) {
            return base.withScores(
                vectorScore,
                lexicalScore,
                exactScore,
                normalizedFusionScore,
                channels
            );
        }

        private Double maximum(Double current, double candidate) {
            return current == null ? candidate : Math.max(current, candidate);
        }
    }
}
