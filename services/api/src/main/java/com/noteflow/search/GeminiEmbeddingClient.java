package com.noteflow.search;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class GeminiEmbeddingClient implements EmbeddingClient {
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final String provider;
    private final String geminiApiKey;
    private final String geminiModel;
    private final String openAiApiKey;
    private final String openAiModel;

    public GeminiEmbeddingClient(
            ObjectMapper objectMapper,
            @Value("${noteflow.embedding.provider:${EMBEDDING_PROVIDER:disabled}}") String provider,
            @Value("${noteflow.embedding.gemini-api-key:${GEMINI_API_KEY:}}") String geminiApiKey,
            @Value("${noteflow.embedding.gemini-model:${GEMINI_EMBEDDING_MODEL:gemini-embedding-001}}") String geminiModel,
            @Value("${noteflow.embedding.openai-api-key:${OPENAI_API_KEY:}}") String openAiApiKey,
            @Value("${noteflow.embedding.openai-model:${OPENAI_EMBEDDING_MODEL:text-embedding-3-small}}") String openAiModel) {
        this.objectMapper = objectMapper;
        this.httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(20))
            .build();
        this.provider = provider == null ? "disabled" : provider.trim().toLowerCase();
        this.geminiApiKey = geminiApiKey == null ? "" : geminiApiKey.trim();
        this.geminiModel = geminiModel == null || geminiModel.isBlank()
            ? "gemini-embedding-001" : geminiModel.trim();
        this.openAiApiKey = openAiApiKey == null ? "" : openAiApiKey.trim();
        this.openAiModel = openAiModel == null || openAiModel.isBlank()
            ? "text-embedding-3-small" : openAiModel.trim();
    }

    @Override
    public String providerName() {
        return provider;
    }

    @Override
    public String model() {
        return switch (provider) {
            case "gemini" -> geminiModel;
            case "openai" -> openAiModel;
            default -> "none";
        };
    }

    @Override
    public float[] embed(String text) {
        return switch (provider) {
            case "gemini" -> embedGemini(text);
            case "openai" -> embedOpenAi(text);
            case "local" -> throw new IllegalStateException(
                "Local query embedding is reserved but not configured in the Java API."
            );
            default -> throw new IllegalStateException("Search embedding provider is not configured.");
        };
    }

    private float[] embedGemini(String text) {
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
            this(model, new Content(new Part[] {new Part(text)}));
        }
    }

    private record Content(Part[] parts) {
    }

    private record Part(String text) {
    }
}
