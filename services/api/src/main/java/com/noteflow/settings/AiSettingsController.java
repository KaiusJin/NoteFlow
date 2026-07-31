package com.noteflow.settings;

import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AiSettingsController {
    private static final Set<String> PROVIDERS = Set.of("auto", "gemini", "openai", "disabled");
    private static final int MAX_API_KEY_LENGTH = 512;
    private static final int MAX_MODEL_ID_LENGTH = 128;
    private static final Pattern MODEL_ID = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:/-]*");

    private final AiSettingsService service;

    public AiSettingsController(AiSettingsService service) {
        this.service = service;
    }

    @GetMapping("/settings/ai")
    public AiSettingsResponse get() {
        return toResponse(service.loadOrCreate());
    }

    /**
     * Partial update: null fields keep their current value; an empty string
     * clears the field (falling back to environment defaults).
     */
    @PutMapping("/settings/ai")
    public AiSettingsResponse update(@RequestBody AiSettingsRequest request) {
        AiSettings settings = service.loadOrCreate();
        if (request.geminiApiKey() != null) {
            settings.setGeminiApiKey(validApiKey(request.geminiApiKey()));
        }
        if (request.openaiApiKey() != null) {
            settings.setOpenaiApiKey(validApiKey(request.openaiApiKey()));
        }
        if (request.llmProvider() != null) {
            settings.setLlmProvider(validProvider(request.llmProvider()));
        }
        if (request.geminiLlmModel() != null) {
            settings.setGeminiLlmModel(validModelId(request.geminiLlmModel()));
        }
        if (request.openaiLlmModel() != null) {
            settings.setOpenaiLlmModel(validModelId(request.openaiLlmModel()));
        }
        if (request.embeddingProvider() != null) {
            settings.setEmbeddingProvider(validProvider(request.embeddingProvider()));
        }
        if (request.geminiEmbeddingModel() != null) {
            settings.setGeminiEmbeddingModel(validModelId(request.geminiEmbeddingModel()));
        }
        if (request.openaiEmbeddingModel() != null) {
            settings.setOpenaiEmbeddingModel(validModelId(request.openaiEmbeddingModel()));
        }
        return toResponse(service.save(settings));
    }

    private String validApiKey(String value) {
        String trimmed = value.trim();
        if (trimmed.length() > MAX_API_KEY_LENGTH) {
            throw new IllegalArgumentException("API key is too long");
        }
        if (trimmed.chars().anyMatch(character -> Character.isISOControl(character))) {
            throw new IllegalArgumentException("API key contains control characters");
        }
        return trimmed;
    }

    private String validModelId(String value) {
        String trimmed = value.trim();
        if (trimmed.isEmpty()) {
            return "";
        }
        if (trimmed.length() > MAX_MODEL_ID_LENGTH || !MODEL_ID.matcher(trimmed).matches()) {
            throw new IllegalArgumentException(
                "Model id must be at most 128 characters and contain only letters, digits, '.', '_', ':', '/', or '-'"
            );
        }
        return trimmed;
    }

    private String validProvider(String value) {
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        if (!PROVIDERS.contains(normalized)) {
            throw new IllegalArgumentException("Unknown provider: " + value);
        }
        return normalized;
    }

    private AiSettingsResponse toResponse(AiSettings settings) {
        return new AiSettingsResponse(
            keySet(settings.getGeminiApiKey()),
            keyHint(settings.getGeminiApiKey()),
            keySet(settings.getOpenaiApiKey()),
            keyHint(settings.getOpenaiApiKey()),
            orAuto(settings.getLlmProvider()),
            safe(settings.getGeminiLlmModel()),
            safe(settings.getOpenaiLlmModel()),
            orAuto(settings.getEmbeddingProvider()),
            safe(settings.getGeminiEmbeddingModel()),
            safe(settings.getOpenaiEmbeddingModel()),
            new AiSettingsResponse.Effective(
                service.llmProvider(),
                service.embeddingProvider(),
                service.embeddingModel()
            )
        );
    }

    private static boolean keySet(String key) {
        return key != null && !key.isBlank();
    }

    /** Never return the key itself; only the last 4 characters as a hint. */
    private static String keyHint(String key) {
        if (key == null || key.isBlank()) {
            return "";
        }
        String trimmed = key.trim();
        return trimmed.length() <= 4 ? "****" : "…" + trimmed.substring(trimmed.length() - 4);
    }

    private static String orAuto(String value) {
        return value == null || value.isBlank() ? "auto" : value;
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
