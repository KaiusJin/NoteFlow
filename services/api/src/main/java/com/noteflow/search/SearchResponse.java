package com.noteflow.search;

import java.util.List;

public record SearchResponse(
    String query,
    SearchMode mode,
    List<SearchResultResponse> results
) {
}
