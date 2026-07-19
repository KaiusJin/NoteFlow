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
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class ExternalSemanticReranker {
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;
    private final AiSettingsService aiSettings;
    private final String provider;
    private final String defaultModel;
    private final int timeoutSeconds;
    private final int candidateLimit;

    @Autowired
    ExternalSemanticReranker(
        ObjectMapper objectMapper,
        HttpClient httpClient,
        AiSettingsService aiSettings,
        @Value("${noteflow.retrieval.reranker-provider:${RETRIEVAL_RERANKER_PROVIDER:disabled}}") String provider,
        @Value("${noteflow.retrieval.gemini-reranker-model:${GEMINI_RERANK_MODEL:gemini-2.5-flash}}") String model,
        @Value("${noteflow.retrieval.external-reranker-timeout-seconds:20}") int timeoutSeconds,
        @Value("${noteflow.retrieval.external-reranker-candidate-limit:12}") int candidateLimit
    ) {
        this.objectMapper = objectMapper;
        this.httpClient = httpClient;
        this.aiSettings = aiSettings;
        this.provider = provider == null ? "disabled" : provider.trim().toLowerCase();
        this.defaultModel = model == null || model.isBlank() ? "gemini-2.5-flash" : model.trim();
        this.timeoutSeconds = timeoutSeconds;
        this.candidateLimit = candidateLimit;
    }

    ExternalRerankResult rerank(String query, List<RetrievalCandidate> candidates) {
        long startedAt = System.nanoTime();
        if (!"gemini".equals(provider) || candidates.size() < 2) {
            return new ExternalRerankResult(
                candidates,
                provider,
                false,
                null,
                elapsedMs(startedAt)
            );
        }
        String apiKey = aiSettings.geminiApiKey();
        String model = aiSettings.geminiLlmModel(defaultModel);
        if (apiKey.isBlank()) {
            return fallback(candidates, "GEMINI_API_KEY is not configured.", startedAt);
        }
        try {
            List<RetrievalCandidate> head = candidates.subList(0, Math.min(candidateLimit, candidates.size()));
            String payload = buildPayload(query, head);
            String modelName = model.startsWith("models/") ? model : "models/" + model;
            HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(
                    "https://generativelanguage.googleapis.com/v1beta/"
                        + modelName + ":generateContent?key=" + apiKey
                ))
                .timeout(Duration.ofSeconds(timeoutSeconds))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(payload, StandardCharsets.UTF_8))
                .build();
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                return fallback(candidates, "HTTP " + response.statusCode(), startedAt);
            }
            String rankingJson = responseText(response.body());
            List<RetrievalCandidate> rerankedHead = applyRanking(head, rankingJson);
            List<RetrievalCandidate> merged = new ArrayList<>(rerankedHead);
            merged.addAll(candidates.subList(head.size(), candidates.size()));
            return new ExternalRerankResult(
                List.copyOf(merged),
                provider,
                true,
                null,
                elapsedMs(startedAt)
            );
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return fallback(candidates, "interrupted", startedAt);
        } catch (Exception error) {
            return fallback(candidates, conciseError(error), startedAt);
        }
    }

    List<RetrievalCandidate> applyRanking(
        List<RetrievalCandidate> candidates,
        String rankingJson
    ) throws Exception {
        JsonNode root = objectMapper.readTree(rankingJson);
        if (!root.isArray()) {
            throw new IllegalArgumentException("Reranker output must be a JSON array.");
        }
        Map<String, RetrievalCandidate> byId = new LinkedHashMap<>();
        for (int index = 0; index < candidates.size(); index++) {
            byId.put("C" + (index + 1), candidates.get(index));
        }
        Map<String, Double> scores = new HashMap<>();
        for (JsonNode item : root) {
            String id = item.path("id").asText();
            if (byId.containsKey(id) && item.has("score")) {
                scores.put(id, item.path("score").asDouble());
            }
        }
        if (scores.isEmpty()) {
            throw new IllegalArgumentException("Reranker output contains no valid candidate IDs.");
        }
        List<Map.Entry<String, RetrievalCandidate>> entries = new ArrayList<>(byId.entrySet());
        entries.sort(
            Comparator.<Map.Entry<String, RetrievalCandidate>>comparingDouble(
                entry -> scores.getOrDefault(entry.getKey(), Double.NEGATIVE_INFINITY)
            ).reversed()
        );
        return entries.stream().map(Map.Entry::getValue).toList();
    }

    private String buildPayload(String query, List<RetrievalCandidate> candidates) throws Exception {
        StringBuilder prompt = new StringBuilder(
            """
            Rank the evidence candidates by how directly and completely they answer the query.
            Do not add candidates or facts. Prefer exact definitions, formulas, code, and primary
            PDF evidence when relevance is otherwise similar.

            Query:
            """
        );
        prompt.append(query).append("\n\nCandidates:\n");
        for (int index = 0; index < candidates.size(); index++) {
            RetrievalCandidate candidate = candidates.get(index);
            prompt.append("C").append(index + 1)
                .append(" | ").append(candidate.sourceDomain())
                .append(" | ").append(candidate.title())
                .append(" | pages ").append(candidate.pageStart()).append("-").append(candidate.pageEnd())
                .append("\n")
                .append(bounded(candidate.content(), 1800))
                .append("\n\n");
        }
        Map<String, Object> generationConfig = Map.of(
            "responseMimeType", "application/json",
            "responseSchema", Map.of(
                "type", "ARRAY",
                "items", Map.of(
                    "type", "OBJECT",
                    "properties", Map.of(
                        "id", Map.of("type", "STRING"),
                        "score", Map.of("type", "NUMBER")
                    ),
                    "required", List.of("id", "score")
                )
            )
        );
        Map<String, Object> payload = Map.of(
            "contents", List.of(Map.of(
                "role", "user",
                "parts", List.of(Map.of("text", prompt.toString()))
            )),
            "generationConfig", generationConfig
        );
        return objectMapper.writeValueAsString(payload);
    }

    private String responseText(String responseBody) throws Exception {
        JsonNode response = objectMapper.readTree(responseBody);
        String text = response.path("candidates").path(0).path("content").path("parts").path(0).path("text").asText();
        if (text.isBlank()) {
            throw new IllegalArgumentException("Gemini reranker returned no text.");
        }
        return text;
    }

    private ExternalRerankResult fallback(
        List<RetrievalCandidate> candidates,
        String error,
        long startedAt
    ) {
        return new ExternalRerankResult(
            candidates,
            provider,
            false,
            error,
            elapsedMs(startedAt)
        );
    }

    private String bounded(String content, int maximumCharacters) {
        if (content == null) {
            return "";
        }
        return content.length() <= maximumCharacters
            ? content
            : content.substring(0, maximumCharacters);
    }

    private String conciseError(Throwable error) {
        String message = error.getMessage();
        return error.getClass().getSimpleName() + (message == null ? "" : ": " + message);
    }

    private long elapsedMs(long startedAt) {
        return Math.max(0, (System.nanoTime() - startedAt) / 1_000_000);
    }
}
