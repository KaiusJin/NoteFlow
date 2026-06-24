package com.noteflow.retrieval;

import com.noteflow.chunks.DocumentChunk;
import com.noteflow.chunks.DocumentChunkRepository;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
class ContextBuilder {
    private final DocumentChunkRepository chunks;
    private final int maxTokensPerItem;

    ContextBuilder(
        DocumentChunkRepository chunks,
        @Value("${noteflow.retrieval.max-tokens-per-item:1400}") int maxTokensPerItem
    ) {
        this.chunks = chunks;
        this.maxTokensPerItem = maxTokensPerItem;
    }

    ContextBuildResult build(List<RetrievalCandidate> candidates, int maxItems, int maxContextTokens) {
        List<RetrievalItemResponse> items = new ArrayList<>();
        Set<UUID> consumedSourceIds = new HashSet<>();
        Map<UUID, List<DocumentChunk>> chunkCache = new HashMap<>();
        int totalTokens = 0;

        for (RetrievalCandidate candidate : candidates) {
            if (items.size() >= maxItems || totalTokens >= maxContextTokens) {
                break;
            }
            if (consumedSourceIds.contains(candidate.sourceObjectId())) {
                continue;
            }

            ExpandedEvidence evidence = expand(candidate, chunkCache);
            int remainingTokens = maxContextTokens - totalTokens;
            if (remainingTokens <= 0) {
                break;
            }
            int itemBudget = Math.min(maxTokensPerItem, remainingTokens);
            TruncatedContent bounded = truncateToTokens(evidence.content(), itemBudget);
            if (bounded.content().isBlank()) {
                continue;
            }

            int itemTokens = estimateTokens(bounded.content());
            String citationId = "S" + (items.size() + 1);
            items.add(new RetrievalItemResponse(
                citationId,
                candidate.sourceDomain(),
                candidate.sourceObjectType(),
                candidate.documentId(),
                candidate.documentTitle(),
                evidence.pageStart(),
                evidence.pageEnd(),
                evidence.sourceObjectIds(),
                candidate.title(),
                bounded.content(),
                itemTokens,
                candidate.score(),
                candidate.vectorScore(),
                candidate.lexicalScore(),
                candidate.exactScore(),
                candidate.fusionScore(),
                candidate.matchedChannels(),
                bounded.truncated()
            ));
            consumedSourceIds.addAll(evidence.sourceObjectIds());
            totalTokens += itemTokens;
        }
        return new ContextBuildResult(items, totalTokens);
    }

    private ExpandedEvidence expand(
        RetrievalCandidate candidate,
        Map<UUID, List<DocumentChunk>> chunkCache
    ) {
        if (!"PDF".equals(candidate.sourceDomain()) || candidate.chunkIndex() == null) {
            return ExpandedEvidence.fromCandidate(candidate);
        }

        List<DocumentChunk> documentChunks = chunkCache.computeIfAbsent(
            candidate.documentId(),
            chunks::findByDocumentIdOrderByChunkIndexAsc
        );
        int selectedPosition = -1;
        for (int index = 0; index < documentChunks.size(); index++) {
            if (documentChunks.get(index).getId().equals(candidate.sourceObjectId())) {
                selectedPosition = index;
                break;
            }
        }
        if (selectedPosition < 0) {
            return ExpandedEvidence.fromCandidate(candidate);
        }

        List<DocumentChunk> selected = new ArrayList<>();
        DocumentChunk center = documentChunks.get(selectedPosition);
        selected.add(center);
        int estimatedTokens = chunkTokens(center);

        if (selectedPosition > 0) {
            DocumentChunk previous = documentChunks.get(selectedPosition - 1);
            if (compatible(previous, center) && estimatedTokens + chunkTokens(previous) <= maxTokensPerItem) {
                selected.add(previous);
                estimatedTokens += chunkTokens(previous);
            }
        }
        if (selectedPosition + 1 < documentChunks.size()) {
            DocumentChunk next = documentChunks.get(selectedPosition + 1);
            if (compatible(center, next) && estimatedTokens + chunkTokens(next) <= maxTokensPerItem) {
                selected.add(next);
            }
        }
        selected.sort(Comparator.comparingInt(DocumentChunk::getChunkIndex));

        String content = selected.stream()
            .map(DocumentChunk::getContent)
            .filter(value -> value != null && !value.isBlank())
            .reduce((left, right) -> left + "\n\n" + right)
            .orElse(candidate.content());
        Integer pageStart = selected.stream()
            .map(this::pageStart)
            .filter(value -> value != null)
            .min(Integer::compareTo)
            .orElse(candidate.pageStart());
        Integer pageEnd = selected.stream()
            .map(this::pageEnd)
            .filter(value -> value != null)
            .max(Integer::compareTo)
            .orElse(candidate.pageEnd());
        List<UUID> sourceIds = selected.stream().map(DocumentChunk::getId).toList();
        return new ExpandedEvidence(content, pageStart, pageEnd, sourceIds);
    }

    private boolean compatible(DocumentChunk left, DocumentChunk right) {
        if (right.getChunkIndex() - left.getChunkIndex() != 1) {
            return false;
        }
        String leftTitle = normalizeTitle(left.getSectionTitle());
        String rightTitle = normalizeTitle(right.getSectionTitle());
        if (leftTitle.isBlank() || rightTitle.isBlank() || !leftTitle.equals(rightTitle)) {
            return false;
        }
        Integer leftEnd = pageEnd(left);
        Integer rightStart = pageStart(right);
        return leftEnd == null || rightStart == null || rightStart - leftEnd <= 1;
    }

    private String normalizeTitle(String value) {
        if (value == null || value.matches("(?i)chunk\\s+\\d+")) {
            return "";
        }
        return value.toLowerCase(Locale.ROOT).replaceAll("\\s+", " ").strip();
    }

    private int chunkTokens(DocumentChunk chunk) {
        return chunk.getTokenCount() == null ? estimateTokens(chunk.getContent()) : chunk.getTokenCount();
    }

    private Integer pageStart(DocumentChunk chunk) {
        return chunk.getPageStart() == null ? chunk.getPageNumber() : chunk.getPageStart();
    }

    private Integer pageEnd(DocumentChunk chunk) {
        Integer start = pageStart(chunk);
        return chunk.getPageEnd() == null ? start : chunk.getPageEnd();
    }

    private TruncatedContent truncateToTokens(String content, int tokenBudget) {
        int estimated = estimateTokens(content);
        if (estimated <= tokenBudget) {
            return new TruncatedContent(content, false);
        }
        int maxCharacters = Math.max(1, tokenBudget * 4);
        int end = Math.min(content.length(), maxCharacters);
        while (end > 0 && end < content.length() && !Character.isWhitespace(content.charAt(end - 1))) {
            end--;
        }
        if (end == 0) {
            end = Math.min(content.length(), maxCharacters);
        }
        String truncated = content.substring(0, end).stripTrailing();
        while (estimateTokens(truncated) > tokenBudget && truncated.length() > 1) {
            int reducedEnd = Math.max(1, (int) Math.floor(truncated.length() * 0.9));
            truncated = truncated.substring(0, reducedEnd).stripTrailing();
        }
        return new TruncatedContent(truncated, true);
    }

    static int estimateTokens(String content) {
        if (content == null || content.isBlank()) {
            return 0;
        }
        int characterEstimate = (int) Math.ceil(content.length() / 4.0);
        int wordEstimate = (int) Math.ceil(content.strip().split("\\s+").length * 1.3);
        return Math.max(characterEstimate, wordEstimate);
    }

    record ContextBuildResult(List<RetrievalItemResponse> items, int tokenCount) {
    }

    private record ExpandedEvidence(
        String content,
        Integer pageStart,
        Integer pageEnd,
        List<UUID> sourceObjectIds
    ) {
        static ExpandedEvidence fromCandidate(RetrievalCandidate candidate) {
            return new ExpandedEvidence(
                candidate.content(),
                candidate.pageStart(),
                candidate.pageEnd(),
                List.of(candidate.sourceObjectId())
            );
        }
    }

    private record TruncatedContent(String content, boolean truncated) {
    }
}
