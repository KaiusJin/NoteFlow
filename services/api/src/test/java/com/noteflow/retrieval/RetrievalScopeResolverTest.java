package com.noteflow.retrieval;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.search.SearchMode;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.Optional;
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
        when(documents.findById(readyId)).thenReturn(Optional.of(ready));
        when(documents.findById(processingId)).thenReturn(Optional.of(processing));
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
    void customScopeRejectsForeignDocument() {
        LocalWorkspaceService users = mock(LocalWorkspaceService.class);
        DocumentRepository documents = mock(DocumentRepository.class);
        UUID foreignId = UUID.randomUUID();
        Document foreign = document(foreignId, UUID.randomUUID(), DocumentStatus.READY);
        when(users.currentUserId()).thenReturn(USER_ID);
        when(documents.findById(foreignId)).thenReturn(Optional.of(foreign));
        RetrievalScopeResolver resolver = new RetrievalScopeResolver(users, documents);

        assertThatThrownBy(() -> resolver.resolve(SearchMode.CUSTOM, List.of(foreignId), List.of()))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Document not found");
    }

    private Document document(UUID id, UUID userId, DocumentStatus status) {
        Document document = mock(Document.class);
        lenient().when(document.getId()).thenReturn(id);
        lenient().when(document.getUserId()).thenReturn(userId);
        lenient().when(document.getStatus()).thenReturn(status);
        return document;
    }
}
