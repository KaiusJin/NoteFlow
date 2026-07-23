package com.noteflow.retrieval;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.search.SearchMode;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
class RetrievalScopeResolver {
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

    private List<UUID> filterOwnedReadyDocuments(List<UUID> ids, UUID userId) {
        return ids.stream()
            .distinct()
            .map(id -> loadCurrentUserDocument(id, userId))
            .filter(document -> document.getStatus() == DocumentStatus.READY)
            .map(Document::getId)
            .toList();
    }

    private Document loadCurrentUserDocument(UUID documentId, UUID userId) {
        return documents.findById(documentId)
            .filter(candidate -> candidate.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
    }
}
