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
    private final String apiKey;
    private final String model;

    public GeminiEmbeddingClient(
            ObjectMapper objectMapper,
            @Value("${noteflow.embedding.provider:${EMBEDDING_PROVIDER:disabled}}") String provider,
            @Value("${noteflow.embedding.gemini-api-key:${GEMINI_API_KEY:}}") String apiKey,
            @Value("${noteflow.embedding.gemini-model:${GEMINI_EMBEDDING_MODEL:gemini-embedding-001}}") String model) {
        this.objectMapper = objectMapper;
        this.httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(20))
            .build();
        this.provider = provider == null ? "disabled" : provider.trim().toLowerCase();
        this.apiKey = apiKey == null ? "" : apiKey.trim();
        this.model = model == null || model.isBlank() ? "gemini-embedding-001" : model.trim();
    }

    @Override
    public String providerName() {
        return provider;
    }

    @Override
    public String model() {
        return model;
    }

    @Override
    public float[] embed(String text) {
        if (!"gemini".equals(provider)) {
            throw new IllegalStateException("Search embedding provider must be gemini for this implementation.");
        }
        if (apiKey.isBlank()) {
            throw new IllegalStateException("GEMINI_API_KEY is not configured for search embeddings.");
        }
        try {
            String modelName = model.startsWith("models/") ? model : "models/" + model;
            String payload = objectMapper.writeValueAsString(new GeminiEmbeddingRequest(modelName, text));
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create("https://generativelanguage.googleapis.com/v1beta/" + modelName + ":embedContent?key=" + apiKey))
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
