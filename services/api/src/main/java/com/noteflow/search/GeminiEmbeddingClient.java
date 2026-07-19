package com.noteflow.search;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.settings.AiSettingsService;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import org.springframework.stereotype.Component;

/**
 * Provider, model, and API key are resolved per call through
 * {@link AiSettingsService}, so user-saved settings take effect without a
 * restart.
 */
@Component
public class GeminiEmbeddingClient implements EmbeddingClient {
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final AiSettingsService aiSettings;

    public GeminiEmbeddingClient(
            ObjectMapper objectMapper,
            HttpClient externalHttpClient,
            AiSettingsService aiSettings) {
        this.objectMapper = objectMapper;
        this.httpClient = externalHttpClient;
        this.aiSettings = aiSettings;
    }

    @Override
    public String providerName() {
        return aiSettings.embeddingProvider();
    }

    @Override
    public String model() {
        return aiSettings.embeddingModel();
    }

    @Override
    public float[] embed(String text) {
        return switch (providerName()) {
            case "gemini" -> embedGemini(text);
            case "openai" -> embedOpenAi(text);
            case "local" -> throw new IllegalStateException(
                "Local query embedding is reserved but not configured in the Java API."
            );
            default -> throw new IllegalStateException("Search embedding provider is not configured.");
        };
    }

    private float[] embedGemini(String text) {
        String geminiApiKey = aiSettings.geminiApiKey();
        String geminiModel = aiSettings.embeddingModel();
        if (geminiApiKey.isBlank()) {
            throw new IllegalStateException("GEMINI_API_KEY is not configured for search embeddings.");
        }
        try {
            String modelName = geminiModel.startsWith("models/") ? geminiModel : "models/" + geminiModel;
            String payload = objectMapper.writeValueAsString(new GeminiEmbeddingRequest(modelName, text));
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(
                    "https://generativelanguage.googleapis.com/v1beta/"
                        + modelName + ":embedContent?key=" + geminiApiKey
                ))
                .timeout(Duration.ofSeconds(120))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
                .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new IllegalStateException("Gemini embedding request failed: HTTP " + response.statusCode() + " " + response.body());
            }
            JsonNode values = objectMapper.readTree(response.body()).path("embedding").path("values");
            if (!values.isArray() || values.isEmpty()) {
                throw new IllegalStateException("Gemini embedding response did not contain embedding.values.");
            }
            float[] vector = new float[values.size()];
            for (int index = 0; index < values.size(); index++) {
                vector[index] = (float) values.get(index).asDouble();
            }
            return vector;
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Gemini embedding request was interrupted.", ex);
        } catch (Exception ex) {
            throw new IllegalStateException("Gemini embedding request failed: " + ex.getMessage(), ex);
        }
    }

    private float[] embedOpenAi(String text) {
        String openAiApiKey = aiSettings.openaiApiKey();
        String openAiModel = aiSettings.embeddingModel();
        if (openAiApiKey.isBlank()) {
            throw new IllegalStateException("OPENAI_API_KEY is not configured for search embeddings.");
        }
        try {
            String payload = objectMapper.writeValueAsString(
                java.util.Map.of("model", openAiModel, "input", text)
            );
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create("https://api.openai.com/v1/embeddings"))
                .timeout(Duration.ofSeconds(120))
                .header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + openAiApiKey)
                .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
                .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new IllegalStateException("OpenAI embedding request failed: HTTP " + response.statusCode());
            }
            JsonNode values = objectMapper.readTree(response.body()).path("data").path(0).path("embedding");
            if (!values.isArray() || values.isEmpty()) {
                throw new IllegalStateException("OpenAI embedding response did not contain data[0].embedding.");
            }
            float[] vector = new float[values.size()];
            for (int index = 0; index < values.size(); index++) {
                vector[index] = (float) values.get(index).asDouble();
            }
            return vector;
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("OpenAI embedding request was interrupted.", ex);
        } catch (Exception ex) {
            throw new IllegalStateException("OpenAI embedding request failed: " + ex.getMessage(), ex);
        }
    }

    private record GeminiEmbeddingRequest(String model, Content content) {
        GeminiEmbeddingRequest(String model, String text) {
            this(model, new Content(java.util.List.of(new Part(text))));
        }

        record Content(java.util.List<Part> parts) {
        }

        record Part(String text) {
        }
    }
}
