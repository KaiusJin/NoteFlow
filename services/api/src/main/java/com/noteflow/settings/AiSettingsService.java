package com.noteflow.settings;

import com.noteflow.users.DevUserService;
import java.util.Locale;
import java.util.Optional;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

/**
 * Resolves the effective AI provider configuration: user-saved settings from
 * {@code user_ai_settings} take precedence, environment values are the
 * fallback. Reads go through a short-lived snapshot so per-request callers
 * (retrieval channels, embedding client) do not hit the database each call.
 */
@Service
public class AiSettingsService {
    private static final long CACHE_TTL_MILLIS = 15_000;

    private final AiSettingsRepository repository;
    private final DevUserService users;
    private final String envGeminiApiKey;
    private final String envOpenaiApiKey;
    private final String envEmbeddingProvider;
    private final String envGeminiEmbeddingModel;
    private final String envOpenaiEmbeddingModel;

    private volatile Snapshot cached;

    public AiSettingsService(
        AiSettingsRepository repository,
        DevUserService users,
        @Value("${noteflow.embedding.gemini-api-key:${GEMINI_API_KEY:}}") String envGeminiApiKey,
        @Value("${noteflow.retrieval.openai-api-key:${OPENAI_API_KEY:}}") String envOpenaiApiKey,
        @Value("${noteflow.embedding.provider:${EMBEDDING_PROVIDER:disabled}}") String envEmbeddingProvider,
        @Value("${noteflow.embedding.gemini-model:${GEMINI_EMBEDDING_MODEL:gemini-embedding-001}}") String envGeminiEmbeddingModel,
        @Value("${noteflow.embedding.openai-model:${OPENAI_EMBEDDING_MODEL:text-embedding-3-small}}") String envOpenaiEmbeddingModel
    ) {
        this.repository = repository;
        this.users = users;
        this.envGeminiApiKey = safe(envGeminiApiKey);
        this.envOpenaiApiKey = safe(envOpenaiApiKey);
        this.envEmbeddingProvider = normalizeProvider(envEmbeddingProvider, "disabled");
        this.envGeminiEmbeddingModel = safe(envGeminiEmbeddingModel);
        this.envOpenaiEmbeddingModel = safe(envOpenaiEmbeddingModel);
    }

    public AiSettings loadOrCreate() {
        UUID userId = users.currentUserId();
        return repository.findById(userId).orElseGet(() -> repository.save(new AiSettings(userId)));
    }

    public AiSettings save(AiSettings settings) {
        AiSettings saved = repository.save(settings);
        cached = null;
        return saved;
    }

    public String geminiApiKey() {
        return firstNonBlank(snapshot().geminiApiKey(), envGeminiApiKey);
    }

    public String openaiApiKey() {
        return firstNonBlank(snapshot().openaiApiKey(), envOpenaiApiKey);
    }

    /** Resolved to gemini | openai | disabled. */
    public String embeddingProvider() {
        String configured = snapshot().embeddingProvider();
        if (configured.isBlank() || "auto".equals(configured)) {
            // No explicit user choice: prefer the environment's setting, then
            // fall back to whichever provider has a key available.
            if (!"disabled".equals(envEmbeddingProvider) && snapshot().empty()) {
                return envEmbeddingProvider;
            }
            if (!geminiApiKey().isBlank()) {
                return "gemini";
            }
            if (!openaiApiKey().isBlank()) {
                return "openai";
            }
            return envEmbeddingProvider;
        }
        return configured;
    }

    public String embeddingModel() {
        return switch (embeddingProvider()) {
            case "gemini" -> firstNonBlank(snapshot().geminiEmbeddingModel(), envGeminiEmbeddingModel, "gemini-embedding-001");
            case "openai" -> firstNonBlank(snapshot().openaiEmbeddingModel(), envOpenaiEmbeddingModel, "text-embedding-3-small");
            default -> "none";
        };
    }

    /** Resolved to gemini | openai | disabled. */
    public String llmProvider() {
        String configured = snapshot().llmProvider();
        if (configured.isBlank() || "auto".equals(configured)) {
            if (!geminiApiKey().isBlank()) {
                return "gemini";
            }
            if (!openaiApiKey().isBlank()) {
                return "openai";
            }
            return "disabled";
        }
        return configured;
    }

    public String geminiLlmModel(String fallback) {
        return firstNonBlank(snapshot().geminiLlmModel(), fallback);
    }

    public String openaiLlmModel(String fallback) {
        return firstNonBlank(snapshot().openaiLlmModel(), fallback);
    }

    private Snapshot snapshot() {
        Snapshot current = cached;
        long now = System.currentTimeMillis();
        if (current != null && now - current.loadedAt() < CACHE_TTL_MILLIS) {
            return current;
        }
        Optional<AiSettings> row = repository.findById(users.currentUserId());
        Snapshot fresh = row.map(settings -> new Snapshot(
            safe(settings.getGeminiApiKey()),
            safe(settings.getOpenaiApiKey()),
            normalizeProvider(settings.getLlmProvider(), ""),
            safe(settings.getGeminiLlmModel()),
            safe(settings.getOpenaiLlmModel()),
            normalizeProvider(settings.getEmbeddingProvider(), ""),
            safe(settings.getGeminiEmbeddingModel()),
            safe(settings.getOpenaiEmbeddingModel()),
            false,
            now
        )).orElseGet(() -> new Snapshot("", "", "", "", "", "", "", "", true, now));
        cached = fresh;
        return fresh;
    }

    private static String normalizeProvider(String value, String fallback) {
        String normalized = safe(value).toLowerCase(Locale.ROOT);
        return normalized.isBlank() ? fallback : normalized;
    }

    private static String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value;
            }
        }
        return "";
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    private record Snapshot(
        String geminiApiKey,
        String openaiApiKey,
        String llmProvider,
        String geminiLlmModel,
        String openaiLlmModel,
        String embeddingProvider,
        String geminiEmbeddingModel,
        String openaiEmbeddingModel,
        boolean empty,
        long loadedAt
    ) {
    }
}
