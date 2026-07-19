package com.noteflow.settings;

public record AiSettingsResponse(
    boolean geminiKeySet,
    String geminiKeyHint,
    boolean openaiKeySet,
    String openaiKeyHint,
    String llmProvider,
    String geminiLlmModel,
    String openaiLlmModel,
    String embeddingProvider,
    String geminiEmbeddingModel,
    String openaiEmbeddingModel,
    Effective effective
) {
    public record Effective(
        String llmProvider,
        String embeddingProvider,
        String embeddingModel
    ) {
    }
}
