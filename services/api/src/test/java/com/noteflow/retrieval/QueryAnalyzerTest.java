package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class QueryAnalyzerTest {
    private final QueryAnalyzer analyzer = new QueryAnalyzer();

    @Test
    void extractsStructuredSignalsWithoutRewritingOriginalQuery() {
        String query = "Explain Theorem 4.4.10, list_cp_bad, func12, E[X^2], O(n log n), and \"deep copy\"";

        QueryAnalysis analysis = analyzer.analyze(query);

        assertThat(analysis.originalQuery()).isEqualTo(query);
        assertThat(analysis.lexicalQuery()).contains("theorem", "list_cp_bad", "func12", "deep", "copy");
        assertThat(analysis.lexicalQuery()).doesNotContain("explain", "and");
        assertThat(analysis.exactSignals()).contains(
            "4.4.10",
            "list_cp_bad",
            "func12",
            "E[X^2]",
            "O(n log n)",
            "deep copy"
        );
    }

    @Test
    void leavesOrdinaryNaturalLanguageWithoutForcedExactSignals() {
        QueryAnalysis analysis = analyzer.analyze("Why can sharing nodes cause unexpected mutation?");

        assertThat(analysis.exactSignals()).isEmpty();
        assertThat(analysis.lexicalQuery()).isEqualTo("sharing OR nodes OR cause OR unexpected OR mutation");
    }
}
