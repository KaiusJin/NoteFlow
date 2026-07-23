package com.noteflow.study;

import java.util.List;
import java.util.UUID;

/** One domain request shared by the structured Study UI and Agent adapter. */
public record FlashcardGenerationRequest(
    List<UUID> documentIds,
    List<UUID> sourceChunkIds,
    String section,
    String focus,
    String title,
    Integer count,
    Boolean groupBySection,
    String origin
) {
    public static FlashcardGenerationRequest section(UUID documentId) {
        return new FlashcardGenerationRequest(List.of(documentId), List.of(), null, null, null, null, true, "SECTION");
    }
}
