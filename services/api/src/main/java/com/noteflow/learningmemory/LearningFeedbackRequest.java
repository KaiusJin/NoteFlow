package com.noteflow.learningmemory;

import java.util.UUID;

public record LearningFeedbackRequest(
    String eventId,
    String topic,
    String feedback,
    UUID documentId,
    String mistakeType,
    String detail
) {}
