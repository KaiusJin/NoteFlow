package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.noteflow.chunks.DocumentChunk;
import com.noteflow.chunks.DocumentChunkRepository;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ContextBuilderTest {
    private static final UUID DOCUMENT_ID = UUID.randomUUID();

    @Test
    void expandsOnlyStructurallyCompatibleAdjacentPdfChunks() {
        DocumentChunkRepository repository = mock(DocumentChunkRepository.class);
        DocumentChunk previous = chunk(0, "Taylor Series", 1, 1, "Previous derivation.", 10);
        DocumentChunk center = chunk(1, "Taylor Series", 1, 2, "Lagrange remainder formula.", 10);
        DocumentChunk nextDifferentSection = chunk(2, "Worked Example", 2, 2, "Unrelated example.", 10);
        when(repository.findByDocumentIdOrderByChunkIndexAsc(DOCUMENT_ID))
            .thenReturn(List.of(previous, center, nextDifferentSection));
        ContextBuilder builder = new ContextBuilder(repository, 100);
        RetrievalCandidate candidate = candidate(center, "Lagrange remainder formula.", 0.78);

        ContextBuilder.ContextBuildResult result = builder.build(List.of(candidate), 5, 500);

        assertThat(result.items()).singleElement().satisfies(item -> {
            assertThat(item.sourceObjectIds()).containsExactly(previous.getId(), center.getId());
            assertThat(item.content()).contains("Previous derivation.", "Lagrange remainder formula.");
            assertThat(item.content()).doesNotContain("Unrelated example.");
            assertThat(item.pageStart()).isEqualTo(1);
            assertThat(item.pageEnd()).isEqualTo(2);
            assertThat(item.citationId()).isEqualTo("S1");
        });
    }

    @Test
    void enforcesContextAndPerItemTokenBudgets() {
        DocumentChunkRepository repository = mock(DocumentChunkRepository.class);
        ContextBuilder builder = new ContextBuilder(repository, 25);
        String longContent = "Taylor remainder ".repeat(100);
        RetrievalCandidate first = noteCandidate(longContent, 0.80);
        RetrievalCandidate second = noteCandidate("Second useful explanation ".repeat(30), 0.70);

        ContextBuilder.ContextBuildResult result = builder.build(List.of(first, second), 8, 35);

        assertThat(result.tokenCount()).isLessThanOrEqualTo(35);
        assertThat(result.items()).isNotEmpty();
        assertThat(result.items().get(0).truncated()).isTrue();
    }

    private RetrievalCandidate candidate(DocumentChunk chunk, String content, double score) {
        return new RetrievalCandidate(
            "PDF",
            "DOCUMENT_CHUNK",
            chunk.getId(),
            DOCUMENT_ID,
            "MATH138",
            chunk.getPageStart(),
            chunk.getPageEnd(),
            chunk.getSectionTitle(),
            content,
            chunk.getChunkIndex(),
            chunk.getTokenCount(),
            score
        );
    }

    private RetrievalCandidate noteCandidate(String content, double score) {
        return new RetrievalCandidate(
            "AI_NOTE",
            "AI_NOTE_SECTION",
            UUID.randomUUID(),
            DOCUMENT_ID,
            "MATH138",
            1,
            3,
            "Taylor Series",
            content,
            null,
            null,
            score
        );
    }

    private DocumentChunk chunk(
        int index,
        String title,
        int pageStart,
        int pageEnd,
        String content,
        int tokenCount
    ) {
        DocumentChunk chunk = mock(DocumentChunk.class);
        when(chunk.getId()).thenReturn(UUID.randomUUID());
        when(chunk.getDocumentId()).thenReturn(DOCUMENT_ID);
        when(chunk.getChunkIndex()).thenReturn(index);
        when(chunk.getSectionTitle()).thenReturn(title);
        when(chunk.getPageNumber()).thenReturn(pageStart);
        when(chunk.getPageStart()).thenReturn(pageStart);
        when(chunk.getPageEnd()).thenReturn(pageEnd);
        when(chunk.getContent()).thenReturn(content);
        when(chunk.getTokenCount()).thenReturn(tokenCount);
        return chunk;
    }
}
