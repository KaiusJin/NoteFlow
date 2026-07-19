package com.noteflow.settings;

import java.util.Locale;
import java.util.Set;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AiSettingsController {
    private static final Set<String> PROVIDERS = Set.of("auto", "gemini", "openai", "disabled");

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
            settings.setGeminiApiKey(request.geminiApiKey().trim());
        }
        if (request.openaiApiKey() != null) {
            settings.setOpenaiApiKey(request.openaiApiKey().trim());
        }
        if (request.llmProvider() != null) {
            settings.setLlmProvider(validProvider(request.llmProvider()));
        }
        if (request.geminiLlmModel() != null) {
            settings.setGeminiLlmModel(request.geminiLlmModel().trim());
        }
        if (request.openaiLlmModel() != null) {
            settings.setOpenaiLlmModel(request.openaiLlmModel().trim());
        }
        if (request.embeddingProvider() != null) {
            settings.setEmbeddingProvider(validProvider(request.embeddingProvider()));
        }
        if (request.geminiEmbeddingModel() != null) {
            settings.setGeminiEmbeddingModel(request.geminiEmbeddingModel().trim());
        }
        if (request.openaiEmbeddingModel() != null) {
            settings.setOpenaiEmbeddingModel(request.openaiEmbeddingModel().trim());
        }
        return toResponse(service.save(settings));
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
