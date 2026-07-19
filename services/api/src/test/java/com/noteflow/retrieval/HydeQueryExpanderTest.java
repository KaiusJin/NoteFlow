package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.settings.AiSettingsService;
import org.junit.jupiter.api.Test;

class HydeQueryExpanderTest {
    private final HydeQueryExpander expander = new HydeQueryExpander(
        new ObjectMapper(),
        java.net.http.HttpClient.newHttpClient(),
        null,
        "disabled",
        "gemini-2.5-flash",
        "gpt-4o-mini",
        20,
        8
    );

    @Test
    void triggersForShortOrLowInformationQueries() {
        assertThat(expander.shouldExpand("PMF")).isTrue();
        assertThat(expander.shouldExpand("这个是什么")).isTrue();
        assertThat(expander.shouldExpand("explain this concept please")).isTrue();
    }

    @Test
    void doesNotTriggerForSpecificDetailedQueries() {
        assertThat(expander.shouldExpand(
            "Why does list_cp_bad create a shallow copy of linked list nodes?"
        )).isFalse();
        assertThat(expander.shouldExpand(
            "Explain Theorem 4.4.10 Taylor inequality remainder bound"
        )).isFalse();
    }

    @Test
    void disabledProviderRecordsTriggerWithoutInventingExpansion() {
        HydeExpansionResult result = expander.expand("PMF");

        assertThat(result.triggered()).isTrue();
        assertThat(result.generated()).isFalse();
        assertThat(result.hypotheticalDocument()).isNull();
    }

    @Test
    void autoProviderWithoutKeysFallsBackToDisabled() {
        AiSettingsService aiSettings = mock(AiSettingsService.class);
        when(aiSettings.llmProvider()).thenReturn("disabled");
        HydeQueryExpander automatic = new HydeQueryExpander(
            new ObjectMapper(),
            java.net.http.HttpClient.newHttpClient(),
            aiSettings,
            "auto",
            "gemini-2.5-flash",
            "gpt-4o-mini",
            20,
            8
        );

        HydeExpansionResult result = automatic.expand("PMF");

        assertThat(result.provider()).isEqualTo("disabled");
        assertThat(result.triggered()).isTrue();
        assertThat(result.generated()).isFalse();
    }
}
