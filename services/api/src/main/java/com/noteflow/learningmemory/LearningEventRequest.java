package com.noteflow.learningmemory;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public record LearningEventRequest(
    String eventId,
    String eventType,
    Instant occurredAt,
    List<String> topics,
    UUID documentId,
    String artifactType,
    UUID artifactId,
    Boolean correct,
    String difficulty,
    Integer responseTimeMs,
    Boolean hintUsed,
    String reviewGrade,
    String mistakeType,
    String mistakeSummary,
    Map<String,Object> metadata
) {}
