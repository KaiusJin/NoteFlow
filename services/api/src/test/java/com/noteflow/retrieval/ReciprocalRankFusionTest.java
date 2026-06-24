package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ReciprocalRankFusionTest {
    private final ReciprocalRankFusion fusion = new ReciprocalRankFusion(60, 1.0, 1.0, 1.2);

    @Test
    void rewardsCandidatesThatAppearAcrossMultipleChannels() {
        UUID sharedId = UUID.randomUUID();
        RetrievalCandidate sharedVector = candidate(sharedId, 0.75);
        RetrievalCandidate vectorOnly = candidate(UUID.randomUUID(), 0.82);
        RetrievalCandidate sharedLexical = channelCandidate(sharedId, RetrievalChannel.LEXICAL, 0.30);
        RetrievalCandidate sharedExact = channelCandidate(sharedId, RetrievalChannel.EXACT, 1.0);

        List<RetrievalCandidate> result = fusion.fuse(List.of(
            ChannelRecallResult.success(
                RetrievalChannel.VECTOR,
                List.of(vectorOnly, sharedVector),
                5
            ),
            ChannelRecallResult.success(
                RetrievalChannel.LEXICAL,
                List.of(sharedLexical),
                2
            ),
            ChannelRecallResult.success(
                RetrievalChannel.EXACT,
                List.of(sharedExact),
                1
            )
        ));

        assertThat(result.get(0).sourceObjectId()).isEqualTo(sharedId);
        assertThat(result.get(0).matchedChannels()).containsExactly("VECTOR", "LEXICAL", "EXACT");
        assertThat(result.get(0).fusionScore()).isEqualTo(1.0);
        assertThat(result.get(0).vectorScore()).isEqualTo(0.75);
        assertThat(result.get(0).exactScore()).isEqualTo(1.0);
    }

    @Test
    void ignoresUnavailableChannelsAndKeepsAvailableResults() {
        RetrievalCandidate vector = candidate(UUID.randomUUID(), 0.70);

        List<RetrievalCandidate> result = fusion.fuse(List.of(
            ChannelRecallResult.success(RetrievalChannel.VECTOR, List.of(vector), 3),
            ChannelRecallResult.unavailable(RetrievalChannel.LEXICAL, "database error", 3),
            ChannelRecallResult.unavailable(RetrievalChannel.EXACT, "database error", 3)
        ));

        assertThat(result).singleElement().satisfies(candidate -> {
            assertThat(candidate.sourceObjectId()).isEqualTo(vector.sourceObjectId());
            assertThat(candidate.matchedChannels()).containsExactly("VECTOR");
        });
    }

    private RetrievalCandidate candidate(UUID sourceId, double score) {
        return new RetrievalCandidate(
            "AI_NOTE",
            "AI_NOTE_SECTION",
            sourceId,
            UUID.randomUUID(),
            "Document",
            1,
            2,
            "Title",
            "Relevant content for the query.",
            null,
            null,
            score
        );
    }

    private RetrievalCandidate channelCandidate(
        UUID sourceId,
        RetrievalChannel channel,
        double score
    ) {
        RetrievalCandidate base = candidate(sourceId, score);
        return switch (channel) {
            case VECTOR -> base;
            case LEXICAL -> base.withScores(null, score, null, 0, List.of("LEXICAL"));
            case EXACT -> base.withScores(null, null, score, 0, List.of("EXACT"));
        };
    }
}
