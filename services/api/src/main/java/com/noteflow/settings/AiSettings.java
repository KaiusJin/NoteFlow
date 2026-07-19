package com.noteflow.settings;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * Per-user AI provider configuration. Values here override the environment
 * defaults; empty/null fields fall back to whatever the deployment configured.
 * Shared with the Python worker, which reads this table at task start.
 */
@Entity
@Table(name = "user_ai_settings")
public class AiSettings {
    @Id
    private UUID userId;

    @Column(length = 512)
    private String geminiApiKey;

    @Column(length = 512)
    private String openaiApiKey;

    /** auto | gemini | openai | disabled. auto picks by available key. */
    private String llmProvider;
    private String geminiLlmModel;
    private String openaiLlmModel;

    /** auto | gemini | openai | disabled. */
    private String embeddingProvider;
    private String geminiEmbeddingModel;
    private String openaiEmbeddingModel;

    private Instant updatedAt;

    protected AiSettings() {
    }

    public AiSettings(UUID userId) {
        this.userId = userId;
        this.llmProvider = "auto";
        this.embeddingProvider = "auto";
        this.updatedAt = Instant.now();
    }

    public UUID getUserId() {
        return userId;
    }

    public String getGeminiApiKey() {
        return geminiApiKey;
    }

    public void setGeminiApiKey(String geminiApiKey) {
        this.geminiApiKey = geminiApiKey;
        touch();
    }

    public String getOpenaiApiKey() {
        return openaiApiKey;
    }

    public void setOpenaiApiKey(String openaiApiKey) {
        this.openaiApiKey = openaiApiKey;
        touch();
    }

    public String getLlmProvider() {
        return llmProvider;
    }

    public void setLlmProvider(String llmProvider) {
        this.llmProvider = llmProvider;
        touch();
    }

    public String getGeminiLlmModel() {
        return geminiLlmModel;
    }

    public void setGeminiLlmModel(String geminiLlmModel) {
        this.geminiLlmModel = geminiLlmModel;
        touch();
    }

    public String getOpenaiLlmModel() {
        return openaiLlmModel;
    }

    public void setOpenaiLlmModel(String openaiLlmModel) {
        this.openaiLlmModel = openaiLlmModel;
        touch();
    }

    public String getEmbeddingProvider() {
        return embeddingProvider;
    }

    public void setEmbeddingProvider(String embeddingProvider) {
        this.embeddingProvider = embeddingProvider;
        touch();
    }

    public String getGeminiEmbeddingModel() {
        return geminiEmbeddingModel;
    }

    public void setGeminiEmbeddingModel(String geminiEmbeddingModel) {
        this.geminiEmbeddingModel = geminiEmbeddingModel;
        touch();
    }

    public String getOpenaiEmbeddingModel() {
        return openaiEmbeddingModel;
    }

    public void setOpenaiEmbeddingModel(String openaiEmbeddingModel) {
        this.openaiEmbeddingModel = openaiEmbeddingModel;
        touch();
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    private void touch() {
        this.updatedAt = Instant.now();
    }
}
