package com.noteflow.retrieval;

import java.util.List;

record ChannelRecallResult(
    RetrievalChannel channel,
    List<RetrievalCandidate> candidates,
    boolean available,
    String error,
    long elapsedMs
) {
    static ChannelRecallResult success(
        RetrievalChannel channel,
        List<RetrievalCandidate> candidates,
        long elapsedMs
    ) {
        return new ChannelRecallResult(channel, List.copyOf(candidates), true, null, elapsedMs);
    }

    static ChannelRecallResult unavailable(RetrievalChannel channel, String error, long elapsedMs) {
        return new ChannelRecallResult(channel, List.of(), false, error, elapsedMs);
    }
}
