package com.noteflow.retrieval;

import java.util.List;
import java.util.Locale;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class CandidateQualityFilter {
    private static final List<String> BOILERPLATE = List.of(
        "completely blank",
        "no visible text",
        "no discernible content",
        "image region is blank",
        "the page is blank"
    );

    private final double minimumScore;
    private final double minimumLexicalScore;
    private final double minimumExactScore;
    private final int minimumInformativeCharacters;

    CandidateQualityFilter(
        @Value("${noteflow.retrieval.minimum-score:0.48}") double minimumScore,
        @Value("${noteflow.retrieval.minimum-lexical-score:0.01}") double minimumLexicalScore,
        @Value("${noteflow.retrieval.minimum-exact-score:0.25}") double minimumExactScore,
        @Value("${noteflow.retrieval.minimum-informative-characters:20}") int minimumInformativeCharacters
    ) {
        this.minimumScore = minimumScore;
        this.minimumLexicalScore = minimumLexicalScore;
        this.minimumExactScore = minimumExactScore;
        this.minimumInformativeCharacters = minimumInformativeCharacters;
    }

    List<RetrievalCandidate> filter(List<RetrievalCandidate> candidates) {
        return candidates.stream().filter(this::isUseful).toList();
    }

    private boolean isUseful(RetrievalCandidate candidate) {
        if (!meetsChannelThreshold(candidate) || candidate.content() == null) {
            return false;
        }
        String normalized = candidate.content().strip().replaceAll("\\s+", " ");
        if (normalized.length() < minimumInformativeCharacters) {
            return false;
        }
        String lowered = normalized.toLowerCase(Locale.ROOT);
        return BOILERPLATE.stream().noneMatch(lowered::contains);
    }

    private boolean meetsChannelThreshold(RetrievalCandidate candidate) {
        if (candidate.exactScore() != null && candidate.exactScore() >= minimumExactScore) {
            return true;
        }
        if (candidate.lexicalScore() != null && candidate.lexicalScore() >= minimumLexicalScore) {
            return true;
        }
        return candidate.vectorScore() != null && candidate.vectorScore() >= minimumScore;
    }
}
