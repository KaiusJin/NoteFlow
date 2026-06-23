package com.noteflow.search;

public interface EmbeddingClient {
    String providerName();
    String model();
    float[] embed(String text);
}
