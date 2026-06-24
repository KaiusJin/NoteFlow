package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.lenient;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;

import com.noteflow.search.SearchMode;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class RetrievalServiceTest {
    @Mock
    private RetrievalScopeResolver scopeResolver;
    @Mock
    private VectorCandidateRetriever vectorRetriever;
    @Mock
    private LexicalCandidateRetriever lexicalRetriever;
    @Mock
    private ExactSignalCandidateRetriever exactRetriever;
    @Mock
    private QueryAnalyzer queryAnalyzer;
    @Mock
    private HydeQueryExpander hydeQueryExpander;
    @Mock
    private CandidateQualityFilter qualityFilter;
    @Mock
    private ReciprocalRankFusion fusion;
    @Mock
    private CandidateDeduplicator deduplicator;
    @Mock
    private QueryAwareReranker reranker;
    @Mock
    private ExternalSemanticReranker externalReranker;
    @Mock
    private ContextBuilder contextBuilder;
    @Mock
    private EvidenceEvaluator evidenceEvaluator;

    private RetrievalService service;

    @BeforeEach
    void setUp() {
        service = new RetrievalService(
            scopeResolver,
            vectorRetriever,
            lexicalRetriever,
            exactRetriever,
            queryAnalyzer,
            hydeQueryExpander,
            qualityFilter,
            fusion,
            deduplicator,
            reranker,
            externalReranker,
            contextBuilder,
            evidenceEvaluator,
            Runnable::run,
            30,
            30,
            15,
            20,
            6000
        );
        lenient().when(externalReranker.rerank(anyString(), anyList())).thenAnswer(invocation -> {
            List<RetrievalCandidate> candidates = invocation.getArgument(1);
            return new ExternalRerankResult(candidates, "disabled", false, null, 0);
        });
        lenient().when(hydeQueryExpander.expand(anyString())).thenAnswer(invocation ->
            new HydeExpansionResult(false, false, "disabled", null, null, 0)
        );
    }

    @Test
    void retrievesThirtyCandidatesBeforeBuildingFinalTopKContext() {
        RetrievalScope scope = new RetrievalScope(List.of(UUID.randomUUID()), List.of());
        RetrievalRequest request = new RetrievalRequest("Taylor remainder", 5, SearchMode.PDF, null, null, 4000);
        RetrievalCandidate candidate = candidate();
        RetrievalItemResponse item = item(candidate);
        QueryAnalysis analysis = new QueryAnalysis("Taylor remainder", "taylor OR remainder", List.of());
        when(scopeResolver.resolve(SearchMode.PDF, null, null)).thenReturn(scope);
        when(queryAnalyzer.analyze("Taylor remainder")).thenReturn(analysis);
        when(vectorRetriever.retrieve("Taylor remainder", null, scope, 30)).thenReturn(List.of(candidate));
        when(lexicalRetriever.retrieve(analysis, scope, 30)).thenReturn(List.of());
        when(exactRetriever.retrieve(analysis, scope, 15)).thenReturn(List.of());
        when(qualityFilter.filter(List.of(candidate))).thenReturn(List.of(candidate));
        when(qualityFilter.filter(List.of())).thenReturn(List.of());
        when(fusion.fuse(org.mockito.ArgumentMatchers.anyList())).thenReturn(List.of(candidate));
        when(deduplicator.deduplicate(List.of(candidate))).thenReturn(List.of(candidate));
        when(reranker.rerank("Taylor remainder", List.of(candidate), 5)).thenReturn(List.of(candidate));
        when(contextBuilder.build(List.of(candidate), 5, 4000))
            .thenReturn(new ContextBuilder.ContextBuildResult(List.of(item), 25));
        when(evidenceEvaluator.evaluate(List.of(item))).thenReturn(EvidenceStatus.SUFFICIENT);

        RetrievalResponse response = service.retrieve(request);

        verify(vectorRetriever).retrieve("Taylor remainder", null, scope, 30);
        verify(contextBuilder).build(List.of(candidate), 5, 4000);
        assertThat(response.evidenceStatus()).isEqualTo(EvidenceStatus.SUFFICIENT);
        assertThat(response.contextTokenCount()).isEqualTo(25);
        assertThat(response.diagnostics().vectorCandidateCount()).isEqualTo(1);
    }

    @Test
    void returnsNoResultsForEmptyCustomScopeWithoutEmbeddingCall() {
        RetrievalScope scope = new RetrievalScope(List.of(), List.of());
        RetrievalRequest request = new RetrievalRequest(
            "query",
            8,
            SearchMode.CUSTOM,
            List.of(),
            List.of(),
            null
        );
        when(scopeResolver.resolve(SearchMode.CUSTOM, List.of(), List.of())).thenReturn(scope);

        RetrievalResponse response = service.retrieve(request);

        assertThat(response.evidenceStatus()).isEqualTo(EvidenceStatus.NO_RESULTS);
        verify(vectorRetriever, never()).retrieve("query", scope, 30);
    }

    @Test
    void rejectsBlankQuery() {
        RetrievalRequest request = new RetrievalRequest(" ", 8, SearchMode.MIXED, null, null, null);

        assertThatThrownBy(() -> service.retrieve(request))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Retrieval query is required");
        verify(scopeResolver, never()).resolve(SearchMode.MIXED, null, null);
    }

    @Test
    void continuesWithLexicalRecallWhenVectorProviderFails() {
        RetrievalScope scope = new RetrievalScope(List.of(UUID.randomUUID()), List.of());
        RetrievalRequest request = new RetrievalRequest("Theorem 4.4.10", 5, SearchMode.PDF, null, null, 4000);
        QueryAnalysis analysis = new QueryAnalysis(
            "Theorem 4.4.10",
            "theorem",
            List.of("4.4.10")
        );
        RetrievalCandidate lexicalCandidate = candidate().withScores(
            null,
            0.35,
            null,
            0,
            List.of("LEXICAL")
        );
        RetrievalCandidate fusedCandidate = lexicalCandidate.withScores(
            null,
            0.35,
            null,
            1.0,
            List.of("LEXICAL")
        );
        RetrievalItemResponse item = item(fusedCandidate);
        when(scopeResolver.resolve(SearchMode.PDF, null, null)).thenReturn(scope);
        when(queryAnalyzer.analyze("Theorem 4.4.10")).thenReturn(analysis);
        when(vectorRetriever.retrieve("Theorem 4.4.10", null, scope, 30))
            .thenThrow(new IllegalStateException("embedding unavailable"));
        when(lexicalRetriever.retrieve(analysis, scope, 30))
            .thenReturn(List.of(lexicalCandidate));
        when(exactRetriever.retrieve(analysis, scope, 15)).thenReturn(List.of());
        when(qualityFilter.filter(List.of(lexicalCandidate))).thenReturn(List.of(lexicalCandidate));
        when(qualityFilter.filter(List.of())).thenReturn(List.of());
        when(fusion.fuse(org.mockito.ArgumentMatchers.anyList())).thenReturn(List.of(fusedCandidate));
        when(deduplicator.deduplicate(List.of(fusedCandidate))).thenReturn(List.of(fusedCandidate));
        when(reranker.rerank("Theorem 4.4.10", List.of(fusedCandidate), 5))
            .thenReturn(List.of(fusedCandidate));
        when(contextBuilder.build(List.of(fusedCandidate), 5, 4000))
            .thenReturn(new ContextBuilder.ContextBuildResult(List.of(item), 25));
        when(evidenceEvaluator.evaluate(List.of(item))).thenReturn(EvidenceStatus.WEAK);

        RetrievalResponse response = service.retrieve(request);

        assertThat(response.items()).hasSize(1);
        assertThat(response.diagnostics().channels())
            .anySatisfy(channel -> {
                assertThat(channel.channel()).isEqualTo("VECTOR");
                assertThat(channel.available()).isFalse();
            })
            .anySatisfy(channel -> {
                assertThat(channel.channel()).isEqualTo("LEXICAL");
                assertThat(channel.available()).isTrue();
            });
    }

    @Test
    void failsExplicitlyWhenEveryRecallChannelIsUnavailable() {
        RetrievalScope scope = new RetrievalScope(List.of(UUID.randomUUID()), List.of());
        RetrievalRequest request = new RetrievalRequest("query", 5, SearchMode.PDF, null, null, 4000);
        QueryAnalysis analysis = new QueryAnalysis("query", "query", List.of());
        when(scopeResolver.resolve(SearchMode.PDF, null, null)).thenReturn(scope);
        when(queryAnalyzer.analyze("query")).thenReturn(analysis);
        when(vectorRetriever.retrieve("query", null, scope, 30))
            .thenThrow(new IllegalStateException("vector failed"));
        when(lexicalRetriever.retrieve(analysis, scope, 30))
            .thenThrow(new IllegalStateException("lexical failed"));
        when(exactRetriever.retrieve(analysis, scope, 15))
            .thenThrow(new IllegalStateException("exact failed"));

        assertThatThrownBy(() -> service.retrieve(request))
            .isInstanceOf(IllegalStateException.class)
            .hasMessageContaining("All retrieval channels unavailable")
            .hasMessageContaining("VECTOR")
            .hasMessageContaining("LEXICAL")
            .hasMessageContaining("EXACT");
    }

    private RetrievalCandidate candidate() {
        return new RetrievalCandidate(
            "PDF",
            "DOCUMENT_CHUNK",
            UUID.randomUUID(),
            UUID.randomUUID(),
            "MATH138",
            1,
            2,
            "Taylor Series",
            "Taylor remainder formula.",
            0,
            20,
            0.75
        );
    }

    private RetrievalItemResponse item(RetrievalCandidate candidate) {
        return new RetrievalItemResponse(
            "S1",
            candidate.sourceDomain(),
            candidate.sourceObjectType(),
            candidate.documentId(),
            candidate.documentTitle(),
            candidate.pageStart(),
            candidate.pageEnd(),
            List.of(candidate.sourceObjectId()),
            candidate.title(),
            candidate.content(),
            25,
            candidate.score(),
            candidate.vectorScore(),
            candidate.lexicalScore(),
            candidate.exactScore(),
            candidate.fusionScore(),
            candidate.matchedChannels(),
            false
        );
    }
}
