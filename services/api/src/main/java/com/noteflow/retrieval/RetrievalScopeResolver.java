package com.noteflow.retrieval;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.search.SearchMode;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
class RetrievalScopeResolver {
    private static final Logger log = LoggerFactory.getLogger(RetrievalScopeResolver.class);

    private final LocalWorkspaceService users;
    private final DocumentRepository documents;

    RetrievalScopeResolver(LocalWorkspaceService users, DocumentRepository documents) {
        this.users = users;
        this.documents = documents;
    }

    RetrievalScope resolve(SearchMode mode, List<UUID> pdfDocumentIds, List<UUID> aiNoteDocumentIds) {
        UUID userId = users.currentUserId();
        List<UUID> pdfIds = pdfDocumentIds == null ? List.of() : pdfDocumentIds;
        List<UUID> noteIds = aiNoteDocumentIds == null ? List.of() : aiNoteDocumentIds;

        if (mode == SearchMode.CUSTOM) {
            return new RetrievalScope(
                filterOwnedReadyDocuments(pdfIds, userId),
                filterOwnedReadyDocuments(noteIds, userId)
            );
        }

        List<UUID> allReady = documents.findByUserIdOrderByCreatedAtDesc(userId).stream()
            .filter(document -> document.getStatus() == DocumentStatus.READY)
            .map(Document::getId)
            .toList();
        return switch (mode) {
            case PDF -> new RetrievalScope(allReady, List.of());
            case AI_NOTE -> new RetrievalScope(List.of(), allReady);
            case MIXED -> new RetrievalScope(allReady, allReady);
            case CUSTOM -> throw new IllegalStateException("CUSTOM scope should be resolved explicitly");
        };
    }

    /**
     * Loads all requested documents in a single {@code WHERE id IN} query and
     * filters in memory. Documents that are missing, foreign, or not READY are
     * silently dropped (with a debug log) instead of failing the request.
     */
    private List<UUID> filterOwnedReadyDocuments(List<UUID> ids, UUID userId) {
        Set<UUID> requested = ids.stream().distinct().collect(Collectors.toCollection(LinkedHashSet::new));
        if (requested.isEmpty()) {
            return List.of();
        }
        List<Document> loaded = documents.findByIdInAndUserId(requested, userId);
        Set<UUID> readyIds = loaded.stream()
            .filter(document -> document.getStatus() == DocumentStatus.READY)
            .map(Document::getId)
            .collect(Collectors.toSet());
        for (UUID requestedId : requested) {
            if (!readyIds.contains(requestedId)) {
                log.debug("Excluding unavailable document {} from CUSTOM retrieval scope", requestedId);
            }
        }
        // Preserve the caller's request order, deduplicated.
        return requested.stream().filter(readyIds::contains).toList();
    }
}
