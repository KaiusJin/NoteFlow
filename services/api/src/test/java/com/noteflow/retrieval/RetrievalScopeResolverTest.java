package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.search.SearchMode;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class RetrievalScopeResolverTest {
    private static final UUID USER_ID = UUID.randomUUID();

    @Test
    void customScopeDeduplicatesAndExcludesNonReadyDocuments() {
        LocalWorkspaceService users = mock(LocalWorkspaceService.class);
        DocumentRepository documents = mock(DocumentRepository.class);
        UUID readyId = UUID.randomUUID();
        UUID processingId = UUID.randomUUID();
        Document ready = document(readyId, USER_ID, DocumentStatus.READY);
        Document processing = document(processingId, USER_ID, DocumentStatus.PROCESSING);
        when(users.currentUserId()).thenReturn(USER_ID);
        when(documents.findByIdInAndUserId(org.mockito.ArgumentMatchers.anyCollection(), org.mockito.ArgumentMatchers.eq(USER_ID)))
            .thenReturn(List.of(ready, processing));
        RetrievalScopeResolver resolver = new RetrievalScopeResolver(users, documents);

        RetrievalScope scope = resolver.resolve(
            SearchMode.CUSTOM,
            List.of(readyId, readyId, processingId),
            List.of(readyId)
        );

        assertThat(scope.pdfDocumentIds()).containsExactly(readyId);
        assertThat(scope.aiNoteDocumentIds()).containsExactly(readyId);
    }

    @Test
    void customScopeFiltersForeignDocumentWithoutFailing() {
        LocalWorkspaceService users = mock(LocalWorkspaceService.class);
        DocumentRepository documents = mock(DocumentRepository.class);
        UUID ownedId = UUID.randomUUID();
        UUID foreignId = UUID.randomUUID();
        Document owned = document(ownedId, USER_ID, DocumentStatus.READY);
        Document foreign = document(foreignId, UUID.randomUUID(), DocumentStatus.READY);
        when(users.currentUserId()).thenReturn(USER_ID);
        when(documents.findByIdInAndUserId(org.mockito.ArgumentMatchers.anyCollection(), org.mockito.ArgumentMatchers.eq(USER_ID)))
            .thenReturn(List.of(owned));
        RetrievalScopeResolver resolver = new RetrievalScopeResolver(users, documents);

        RetrievalScope scope = resolver.resolve(SearchMode.CUSTOM, List.of(ownedId, foreignId), List.of());

        assertThat(scope.pdfDocumentIds()).containsExactly(ownedId);
    }

    private Document document(UUID id, UUID userId, DocumentStatus status) {
        Document document = mock(Document.class);
        lenient().when(document.getId()).thenReturn(id);
        lenient().when(document.getUserId()).thenReturn(userId);
        lenient().when(document.getStatus()).thenReturn(status);
        return document;
    }
}
