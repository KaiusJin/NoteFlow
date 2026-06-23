package com.noteflow.search;

import java.util.UUID;

public record GenerateEmbeddingsResponse(
    UUID taskId,
    String status
) {
}
