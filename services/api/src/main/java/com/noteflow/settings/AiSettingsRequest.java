package com.noteflow.settings;

public record AiSettingsRequest(
    String geminiApiKey,
    String openaiApiKey,
    String llmProvider,
    String geminiLlmModel,
    String openaiLlmModel,
    String embeddingProvider,
    String geminiEmbeddingModel,
    String openaiEmbeddingModel
) {
}
