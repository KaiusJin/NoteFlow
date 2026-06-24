package com.noteflow.retrieval;

import com.noteflow.search.SearchMode;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class RetrievalService {
    private static final int MAX_FINAL_RESULTS = 12;

    private final RetrievalScopeResolver scopeResolver;
    private final VectorCandidateRetriever vectorRetriever;
    private final LexicalCandidateRetriever lexicalRetriever;
    private final ExactSignalCandidateRetriever exactRetriever;
    private final QueryAnalyzer queryAnalyzer;
    private final HydeQueryExpander hydeQueryExpander;
    private final CandidateQualityFilter qualityFilter;
    private final ReciprocalRankFusion fusion;
    private final CandidateDeduplicator deduplicator;
    private final QueryAwareReranker reranker;
    private final ExternalSemanticReranker externalReranker;
    private final ContextBuilder contextBuilder;
    private final EvidenceEvaluator evidenceEvaluator;
    private final Executor retrievalExecutor;
    private final int vectorCandidateLimit;
    private final int lexicalCandidateLimit;
    private final int exactCandidateLimit;
    private final int channelTimeoutSeconds;
    private final int defaultContextTokens;

    public RetrievalService(
        RetrievalScopeResolver scopeResolver,
        VectorCandidateRetriever vectorRetriever,
        LexicalCandidateRetriever lexicalRetriever,
        ExactSignalCandidateRetriever exactRetriever,
        QueryAnalyzer queryAnalyzer,
        HydeQueryExpander hydeQueryExpander,
        CandidateQualityFilter qualityFilter,
        ReciprocalRankFusion fusion,
        CandidateDeduplicator deduplicator,
        QueryAwareReranker reranker,
        ExternalSemanticReranker externalReranker,
        ContextBuilder contextBuilder,
        EvidenceEvaluator evidenceEvaluator,
        @Qualifier("retrievalExecutor") Executor retrievalExecutor,
        @Value("${noteflow.retrieval.vector-candidate-limit:30}") int vectorCandidateLimit,
        @Value("${noteflow.retrieval.lexical-candidate-limit:30}") int lexicalCandidateLimit,
        @Value("${noteflow.retrieval.exact-candidate-limit:15}") int exactCandidateLimit,
        @Value("${noteflow.retrieval.channel-timeout-seconds:20}") int channelTimeoutSeconds,
        @Value("${noteflow.retrieval.default-context-tokens:6000}") int defaultContextTokens
    ) {
        this.scopeResolver = scopeResolver;
        this.vectorRetriever = vectorRetriever;
        this.lexicalRetriever = lexicalRetriever;
        this.exactRetriever = exactRetriever;
        this.queryAnalyzer = queryAnalyzer;
        this.hydeQueryExpander = hydeQueryExpander;
        this.qualityFilter = qualityFilter;
        this.fusion = fusion;
        this.deduplicator = deduplicator;
        this.reranker = reranker;
        this.externalReranker = externalReranker;
        this.contextBuilder = contextBuilder;
        this.evidenceEvaluator = evidenceEvaluator;
        this.retrievalExecutor = retrievalExecutor;
        this.vectorCandidateLimit = vectorCandidateLimit;
        this.lexicalCandidateLimit = lexicalCandidateLimit;
        this.exactCandidateLimit = exactCandidateLimit;
        this.channelTimeoutSeconds = channelTimeoutSeconds;
        this.defaultContextTokens = defaultContextTokens;
    }

    public RetrievalResponse retrieve(RetrievalRequest request) {
        long startedAt = System.nanoTime();
        String query = request.query() == null ? "" : request.query().trim();
        if (query.isBlank()) {
            throw new IllegalArgumentException("Retrieval query is required");
        }
        SearchMode mode = request.mode() == null ? SearchMode.MIXED : request.mode();
        int topK = Math.max(1, Math.min(MAX_FINAL_RESULTS, request.topK() == null ? 8 : request.topK()));
        int maxContextTokens = Math.max(
            256,
            Math.min(20_000, request.maxContextTokens() == null ? defaultContextTokens : request.maxContextTokens())
        );
        RetrievalScope scope = scopeResolver.resolve(mode, request.pdfDocumentIds(), request.aiNoteDocumentIds());
        if (scope.isEmpty()) {
            return emptyResponse(query, mode, startedAt);
        }

        QueryAnalysis analysis = queryAnalyzer.analyze(query);
        HydeExpansionResult hyde = hydeQueryExpander.expand(query);
        long recallStartedAt = System.nanoTime();
        CompletableFuture<ChannelRecallResult> vectorFuture = recall(
            RetrievalChannel.VECTOR,
            () -> vectorRetriever.retrieve(
                query,
                hyde.generated() ? hyde.hypotheticalDocument() : null,
                scope,
                vectorCandidateLimit
            )
        );
        CompletableFuture<ChannelRecallResult> lexicalFuture = recall(
            RetrievalChannel.LEXICAL,
            () -> lexicalRetriever.retrieve(analysis, scope, lexicalCandidateLimit)
        );
        CompletableFuture<ChannelRecallResult> exactFuture = recall(
            RetrievalChannel.EXACT,
            () -> exactRetriever.retrieve(analysis, scope, exactCandidateLimit)
        );
        List<ChannelRecallResult> channelResults = List.of(
            vectorFuture.join(),
            lexicalFuture.join(),
            exactFuture.join()
        );
        long recallFinishedAt = System.nanoTime();
        if (channelResults.stream().noneMatch(ChannelRecallResult::available)) {
            String errors = channelResults.stream()
                .map(result -> result.channel().name() + ": " + result.error())
                .reduce((left, right) -> left + "; " + right)
                .orElse("unknown retrieval error");
            throw new IllegalStateException("All retrieval channels unavailable: " + errors);
        }

        List<ChannelRecallResult> filteredChannels = new ArrayList<>();
        int filteredCandidateCount = 0;
        for (ChannelRecallResult result : channelResults) {
            if (!result.available()) {
                filteredChannels.add(result);
                continue;
            }
            List<RetrievalCandidate> filteredCandidates = qualityFilter.filter(result.candidates());
            filteredCandidateCount += filteredCandidates.size();
            filteredChannels.add(ChannelRecallResult.success(
                result.channel(),
                filteredCandidates,
                result.elapsedMs()
            ));
        }

        long fusionStartedAt = System.nanoTime();
        List<RetrievalCandidate> fused = fusion.fuse(filteredChannels);
        List<RetrievalCandidate> deduplicated = deduplicator.deduplicate(fused);
        List<RetrievalCandidate> reranked = reranker.rerank(query, deduplicated, topK);
        ExternalRerankResult externalRerankResult = externalReranker.rerank(query, reranked);
        List<RetrievalCandidate> finalCandidates = externalRerankResult.candidates();
        long fusionFinishedAt = System.nanoTime();

        long contextStartedAt = System.nanoTime();
        ContextBuilder.ContextBuildResult context = contextBuilder.build(
            finalCandidates,
            topK,
            maxContextTokens
        );
        long finishedAt = System.nanoTime();
        EvidenceStatus evidenceStatus = evidenceEvaluator.evaluate(context.items());
        ChannelRecallResult vectorResult = channelResult(channelResults, RetrievalChannel.VECTOR);
        ChannelRecallResult lexicalResult = channelResult(channelResults, RetrievalChannel.LEXICAL);
        ChannelRecallResult exactResult = channelResult(channelResults, RetrievalChannel.EXACT);
        return new RetrievalResponse(
            query,
            mode,
            evidenceStatus,
            context.tokenCount(),
            context.items(),
            new RetrievalDiagnosticsResponse(
                vectorResult.candidates().size(),
                lexicalResult.candidates().size(),
                exactResult.candidates().size(),
                filteredCandidateCount,
                fused.size(),
                deduplicated.size(),
                finalCandidates.size(),
                context.items().size(),
                channelResults.stream().map(this::channelDiagnostics).toList(),
                externalRerankResult.provider(),
                externalRerankResult.applied(),
                externalRerankResult.error(),
                externalRerankResult.elapsedMs(),
                hyde.triggered(),
                hyde.generated(),
                hyde.provider(),
                hyde.error(),
                hyde.elapsedMs(),
                elapsedMs(recallStartedAt, recallFinishedAt),
                elapsedMs(fusionStartedAt, fusionFinishedAt),
                elapsedMs(contextStartedAt, finishedAt),
                elapsedMs(startedAt, finishedAt)
            )
        );
    }

    private RetrievalResponse emptyResponse(String query, SearchMode mode, long startedAt) {
        long finishedAt = System.nanoTime();
        return new RetrievalResponse(
            query,
            mode,
            EvidenceStatus.NO_RESULTS,
            0,
            List.of(),
            new RetrievalDiagnosticsResponse(
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                List.of(),
                "disabled",
                false,
                null,
                0,
                false,
                false,
                "disabled",
                null,
                0,
                0,
                0,
                0,
                elapsedMs(startedAt, finishedAt)
            )
        );
    }

    private CompletableFuture<ChannelRecallResult> recall(
        RetrievalChannel channel,
        Supplier<List<RetrievalCandidate>> supplier
    ) {
        return CompletableFuture.supplyAsync(() -> {
            long startedAt = System.nanoTime();
            try {
                return ChannelRecallResult.success(
                    channel,
                    supplier.get(),
                    elapsedMs(startedAt, System.nanoTime())
                );
            } catch (RuntimeException error) {
                return ChannelRecallResult.unavailable(
                    channel,
                    conciseError(error),
                    elapsedMs(startedAt, System.nanoTime())
                );
            }
        }, retrievalExecutor).orTimeout(channelTimeoutSeconds, TimeUnit.SECONDS).exceptionally(error ->
            ChannelRecallResult.unavailable(
                channel,
                conciseError(error),
                channelTimeoutSeconds * 1000L
            )
        );
    }

    private ChannelRecallResult channelResult(
        List<ChannelRecallResult> results,
        RetrievalChannel channel
    ) {
        return results.stream()
            .filter(result -> result.channel() == channel)
            .findFirst()
            .orElse(ChannelRecallResult.unavailable(channel, "not executed", 0));
    }

    private RetrievalChannelDiagnosticsResponse channelDiagnostics(ChannelRecallResult result) {
        return new RetrievalChannelDiagnosticsResponse(
            result.channel().name(),
            result.available(),
            result.candidates().size(),
            result.elapsedMs(),
            result.error()
        );
    }

    private String conciseError(Throwable error) {
        Throwable current = error;
        while (current.getCause() != null && current.getCause() != current) {
            current = current.getCause();
        }
        String message = current.getMessage();
        return current.getClass().getSimpleName() + (message == null ? "" : ": " + message);
    }

    private long elapsedMs(long start, long end) {
        return Math.max(0, (end - start) / 1_000_000);
    }
}
