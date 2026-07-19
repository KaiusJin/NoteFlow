package com.noteflow.retrieval;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.settings.AiSettingsService;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Locale;
import java.util.Set;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class HydeQueryExpander {
    private static final Set<String> VAGUE_TERMS = Set.of(
        "explain", "help", "this", "that", "it", "thing", "topic", "concept",
        "什么意思", "是什么", "解释", "这个", "怎么", "为什么"
    );

    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final AiSettingsService aiSettings;
    private final String configuredProvider;
    private final String defaultGeminiModel;
    private final String defaultOpenAiModel;
    private final int timeoutSeconds;
    private final int maximumQueryTokens;

    HydeQueryExpander(
        ObjectMapper objectMapper,
        HttpClient externalHttpClient,
        AiSettingsService aiSettings,
        @Value("${noteflow.retrieval.hyde-provider:${HYDE_PROVIDER:auto}}") String provider,
        @Value("${noteflow.retrieval.hyde-gemini-model:${HYDE_GEMINI_MODEL:gemini-2.5-flash}}") String geminiModel,
        @Value("${noteflow.retrieval.hyde-openai-model:${HYDE_OPENAI_MODEL:gpt-4o-mini}}") String openAiModel,
        @Value("${noteflow.retrieval.hyde-timeout-seconds:20}") int timeoutSeconds,
        @Value("${noteflow.retrieval.hyde-max-query-tokens:8}") int maximumQueryTokens
    ) {
        this.objectMapper = objectMapper;
        this.httpClient = externalHttpClient;
        this.aiSettings = aiSettings;
        this.configuredProvider = provider == null
            ? "auto"
            : provider.trim().toLowerCase(Locale.ROOT);
        this.defaultGeminiModel = safeModel(geminiModel, "gemini-2.5-flash");
        this.defaultOpenAiModel = safeModel(openAiModel, "gpt-4o-mini");
        this.timeoutSeconds = timeoutSeconds;
        this.maximumQueryTokens = maximumQueryTokens;
    }

    /**
     * Provider is resolved per call so user-saved settings (keys, provider,
     * model choice) apply without restarting. An explicit non-auto env value
     * still pins the provider.
     */
    private String resolveProvider() {
        if (!"auto".equals(configuredProvider)) {
            return configuredProvider;
        }
        return aiSettings.llmProvider();
    }

    HydeExpansionResult expand(String query) {
        long startedAt = System.nanoTime();
        String provider = resolveProvider();
        if (!shouldExpand(query)) {
            return new HydeExpansionResult(false, false, provider, null, null, elapsedMs(startedAt));
        }
        if ("disabled".equals(provider)) {
            return new HydeExpansionResult(true, false, provider, null, null, elapsedMs(startedAt));
        }
        try {
            String document = switch (provider) {
                case "gemini" -> generateGemini(query);
                case "openai" -> generateOpenAi(query);
                default -> throw new IllegalStateException("Unsupported HyDE provider: " + provider);
            };
            if (document.isBlank()) {
                throw new IllegalStateException("HyDE provider returned an empty hypothetical document.");
            }
            return new HydeExpansionResult(
                true,
                true,
                provider,
                document,
                null,
                elapsedMs(startedAt)
            );
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return fallback(provider, "interrupted", startedAt);
        } catch (Exception error) {
            return fallback(provider, conciseError(error), startedAt);
        }
    }

    boolean shouldExpand(String query) {
        if (query == null || query.isBlank()) {
            return false;
        }
        String normalized = query.toLowerCase(Locale.ROOT).trim();
        int tokens = normalized.split("\\s+").length;
        if (tokens <= 3 || normalized.length() <= 18) {
            return true;
        }
        long informativeTokens = java.util.Arrays.stream(normalized.split("[^\\p{L}\\p{N}_]+"))
            .filter(token -> token.length() > 1 && !VAGUE_TERMS.contains(token))
            .count();
        return tokens <= maximumQueryTokens && informativeTokens <= 2;
    }

    private String generateGemini(String query) throws Exception {
        String geminiApiKey = aiSettings.geminiApiKey();
        String geminiModel = aiSettings.geminiLlmModel(defaultGeminiModel);
        if (geminiApiKey.isBlank()) {
            throw new IllegalStateException("GEMINI_API_KEY is not configured for HyDE.");
        }
        String modelName = geminiModel.startsWith("models/") ? geminiModel : "models/" + geminiModel;
        String payload = objectMapper.writeValueAsString(java.util.Map.of(
            "contents", java.util.List.of(java.util.Map.of(
                "role", "user",
                "parts", java.util.List.of(java.util.Map.of("text", prompt(query)))
            )),
            "generationConfig", java.util.Map.of("temperature", 0.2)
        ));
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(
                "https://generativelanguage.googleapis.com/v1beta/"
                    + modelName + ":generateContent?key=" + geminiApiKey
            ))
            .timeout(Duration.ofSeconds(timeoutSeconds))
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
            .build();
        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        requireSuccess(response);
        return objectMapper.readTree(response.body())
            .path("candidates").path(0).path("content").path("parts").path(0).path("text").asText().trim();
    }

    private String generateOpenAi(String query) throws Exception {
        String openAiApiKey = aiSettings.openaiApiKey();
        String openAiModel = aiSettings.openaiLlmModel(defaultOpenAiModel);
        if (openAiApiKey.isBlank()) {
            throw new IllegalStateException("OPENAI_API_KEY is not configured for HyDE.");
        }
        String payload = objectMapper.writeValueAsString(java.util.Map.of(
            "model", openAiModel,
            "messages", java.util.List.of(java.util.Map.of("role", "user", "content", prompt(query))),
            "temperature", 0.2
        ));
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://api.openai.com/v1/chat/completions"))
            .timeout(Duration.ofSeconds(timeoutSeconds))
            .header("Content-Type", "application/json")
            .header("Authorization", "Bearer " + openAiApiKey)
            .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
            .build();
        HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        requireSuccess(response);
        return objectMapper.readTree(response.body())
            .path("choices").path(0).path("message").path("content").asText().trim();
    }

    private String prompt(String query) {
        return """
            Write one concise hypothetical passage that could appear in a student's course PDF
            or organized study notes and would directly answer the query below. Include likely
            technical vocabulary, formulas, theorem names, or code identifiers when appropriate.
            Do not mention that the passage is hypothetical. Do not cite sources. Return plain
            text only, between 60 and 140 words.

            Query: %s
            """.formatted(query);
    }

    private void requireSuccess(HttpResponse<String> response) {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IllegalStateException("HyDE request failed with HTTP " + response.statusCode());
        }
    }

    private HydeExpansionResult fallback(String provider, String error, long startedAt) {
        return new HydeExpansionResult(true, false, provider, null, error, elapsedMs(startedAt));
    }

    private String conciseError(Throwable error) {
        String message = error.getMessage();
        return error.getClass().getSimpleName() + (message == null ? "" : ": " + message);
    }

    private String safe(String value) {
        return value == null ? "" : value.trim();
    }

    private String safeModel(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value.trim();
    }

    private long elapsedMs(long startedAt) {
        return Math.max(0, (System.nanoTime() - startedAt) / 1_000_000);
    }
}
