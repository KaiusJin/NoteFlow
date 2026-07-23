package com.noteflow.study;

import java.util.List;
import java.util.UUID;

/** One domain request shared by the structured Study UI and Agent adapter. */
public record QuizGenerationRequest(
    List<UUID> documentIds,
    List<UUID> sourceChunkIds,
    String section,
    String focus,
    String title,
    Integer easy,
    Integer medium,
    Integer hard,
    List<String> questionTypes,
    Boolean includeExplanations,
    String origin
) {
    public static QuizGenerationRequest section(UUID documentId, Integer easy, Integer medium, Integer hard) {
        return new QuizGenerationRequest(
            List.of(documentId), List.of(), null, null, null,
            easy == null ? 3 : easy, medium == null ? 5 : medium, hard == null ? 2 : hard,
            List.of("MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER"), true, "SECTION"
        );
    }
}
