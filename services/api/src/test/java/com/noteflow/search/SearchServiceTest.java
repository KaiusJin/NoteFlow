package com.noteflow.search;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.noteflow.documents.Document;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.documents.DocumentStatus;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.users.DevUserService;
import java.sql.ResultSet;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

@ExtendWith(MockitoExtension.class)
class SearchServiceTest {
    private static final UUID USER_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final UUID READY_DOCUMENT_ID = UUID.fromString("10000000-0000-0000-0000-000000000001");
    private static final UUID SECOND_DOCUMENT_ID = UUID.fromString("20000000-0000-0000-0000-000000000002");

    @Mock
    private DevUserService users;
    @Mock
    private DocumentRepository documents;
    @Mock
    private TaskDispatchService taskDispatcher;
    @Mock
    private EmbeddingClient embeddingClient;
    @Mock
    private JdbcTemplate jdbc;

    private SearchService service;

    @BeforeEach
    void setUp() {
        service = new SearchService(users, documents, taskDispatcher, embeddingClient, jdbc);
        when(users.currentUserId()).thenReturn(USER_ID);
        lenient().when(embeddingClient.providerName()).thenReturn("gemini");
        lenient().when(embeddingClient.model()).thenReturn("gemini-embedding-001");
    }

    @Test
    void rejectsBlankQueryBeforeEmbeddingOrDatabaseWork() {
        SearchRequest request = new SearchRequest("   ", 8, SearchMode.MIXED, null, null);

        assertThatThrownBy(() -> service.search(request))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Search query is required");

        verify(embeddingClient, never()).embed(anyString());
        verify(jdbc, never()).query(anyString(), any(RowMapper.class), any(Object[].class));
    }

    @Test
    void emptyCustomScopeReturnsNoResultsWithoutCallingEmbeddingProvider() {
        SearchResponse response = service.search(
            new SearchRequest("probability mass function", 8, SearchMode.CUSTOM, List.of(), List.of())
        );

        assertThat(response.results()).isEmpty();
        assertThat(response.mode()).isEqualTo(SearchMode.CUSTOM);
        verify(embeddingClient, never()).embed(anyString());
        verify(jdbc, never()).query(anyString(), any(RowMapper.class), any(Object[].class));
    }

    @Test
    void pdfModeSearchesOnlyReadyOwnedPdfDocumentsAndCapsTopK() {
        Document ready = document(READY_DOCUMENT_ID, USER_ID, DocumentStatus.READY);
        Document processing = document(SECOND_DOCUMENT_ID, USER_ID, DocumentStatus.PROCESSING);
        when(documents.findByUserIdOrderByCreatedAtDesc(USER_ID)).thenReturn(List.of(ready, processing));
        when(embeddingClient.embed("linked list mutation")).thenReturn(new float[] {0.25f, -0.5f});
        stubEmptySearch();

        service.search(new SearchRequest(" linked list mutation ", 999, SearchMode.PDF, null, null));

        QueryInvocation invocation = captureQueryInvocation();
        assertThat(invocation.sql()).contains("source_domain = 'PDF'");
        assertThat(invocation.sql()).contains("embedding_dimension = ?");
        assertThat(invocation.sql()).doesNotContain("source_domain = 'AI_NOTE'");
        assertThat(invocation.params()).containsExactly(
            "[0.25,-0.5]",
            "gemini",
            "gemini-embedding-001",
            2,
            READY_DOCUMENT_ID,
            "[0.25,-0.5]",
            30
        );
    }

    @Test
    void mixedModeSearchesPdfAndAiNoteForEveryReadyDocument() {
        Document first = document(READY_DOCUMENT_ID, USER_ID, DocumentStatus.READY);
        Document second = document(SECOND_DOCUMENT_ID, USER_ID, DocumentStatus.READY);
        when(documents.findByUserIdOrderByCreatedAtDesc(USER_ID)).thenReturn(List.of(first, second));
        when(embeddingClient.embed("Taylor series")).thenReturn(new float[] {1.0f});
        stubEmptySearch();

        service.search(new SearchRequest("Taylor series", null, SearchMode.MIXED, null, null));

        QueryInvocation invocation = captureQueryInvocation();
        assertThat(invocation.sql()).contains("source_domain = 'PDF'");
        assertThat(invocation.sql()).contains("source_domain = 'AI_NOTE'");
        assertThat(invocation.sql()).contains("embedding_dimension = ?");
        assertThat(invocation.params()).containsExactly(
            "[1.0]",
            "gemini",
            "gemini-embedding-001",
            1,
            READY_DOCUMENT_ID,
            SECOND_DOCUMENT_ID,
            READY_DOCUMENT_ID,
            SECOND_DOCUMENT_ID,
            "[1.0]",
            8
        );
    }

    @Test
    void customModeDeduplicatesSelectionsAndKeepsDomainsSeparate() {
        Document ready = document(READY_DOCUMENT_ID, USER_ID, DocumentStatus.READY);
        Document second = document(SECOND_DOCUMENT_ID, USER_ID, DocumentStatus.READY);
        when(documents.findById(READY_DOCUMENT_ID)).thenReturn(Optional.of(ready));
        when(documents.findById(SECOND_DOCUMENT_ID)).thenReturn(Optional.of(second));
        when(embeddingClient.embed("geometric distribution")).thenReturn(new float[] {0.75f});
        stubEmptySearch();

        service.search(new SearchRequest(
            "geometric distribution",
            0,
            SearchMode.CUSTOM,
            List.of(READY_DOCUMENT_ID, READY_DOCUMENT_ID),
            List.of(SECOND_DOCUMENT_ID)
        ));

        QueryInvocation invocation = captureQueryInvocation();
        assertThat(invocation.sql()).contains("embedding_dimension = ?");
        assertThat(invocation.params()).containsExactly(
            "[0.75]",
            "gemini",
            "gemini-embedding-001",
            1,
            READY_DOCUMENT_ID,
            SECOND_DOCUMENT_ID,
            "[0.75]",
            1
        );
    }

    @Test
    void customModeRejectsDocumentsOwnedByAnotherUser() {
        Document foreign = document(READY_DOCUMENT_ID, UUID.randomUUID(), DocumentStatus.READY);
        when(documents.findById(READY_DOCUMENT_ID)).thenReturn(Optional.of(foreign));

        SearchRequest request = new SearchRequest(
            "private notes",
            8,
            SearchMode.CUSTOM,
            List.of(READY_DOCUMENT_ID),
            List.of()
        );

        assertThatThrownBy(() -> service.search(request))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessage("Document not found");
        verify(embeddingClient, never()).embed(anyString());
    }

    @Test
    void mapsDatabaseResultIncludingSourceAndPageMetadata() throws Exception {
        Document ready = document(READY_DOCUMENT_ID, USER_ID, DocumentStatus.READY);
        when(documents.findByUserIdOrderByCreatedAtDesc(USER_ID)).thenReturn(List.of(ready));
        when(embeddingClient.embed("PMF")).thenReturn(new float[] {0.1f});
        ResultSet row = mock(ResultSet.class);
        UUID sourceObjectId = UUID.randomUUID();
        when(row.getString("source_domain")).thenReturn("AI_NOTE");
        when(row.getString("source_object_type")).thenReturn("AI_NOTE_SECTION");
        when(row.getObject("source_object_id", UUID.class)).thenReturn(sourceObjectId);
        when(row.getObject("document_id", UUID.class)).thenReturn(READY_DOCUMENT_ID);
        when(row.getObject("page_start")).thenReturn(4);
        when(row.getObject("page_end")).thenReturn(6);
        when(row.getString("title")).thenReturn("Probability Mass Function");
        when(row.getString("snippet")).thenReturn("A PMF assigns probability to discrete outcomes.");
        when(row.getDouble("score")).thenReturn(0.82);
        when(row.getString("metadata_json")).thenReturn("{\"sectionType\":\"DEFINITION\"}");
        when(jdbc.query(anyString(), any(RowMapper.class), any(Object[].class))).thenAnswer(invocation -> {
            @SuppressWarnings("unchecked")
            RowMapper<SearchResultResponse> mapper = invocation.getArgument(1);
            return List.of(mapper.mapRow(row, 0));
        });

        SearchResponse response = service.search(new SearchRequest("PMF", 5, SearchMode.AI_NOTE, null, null));

        assertThat(response.results()).singleElement().satisfies(result -> {
            assertThat(result.sourceDomain()).isEqualTo("AI_NOTE");
            assertThat(result.sourceObjectId()).isEqualTo(sourceObjectId);
            assertThat(result.documentId()).isEqualTo(READY_DOCUMENT_ID);
            assertThat(result.pageStart()).isEqualTo(4);
            assertThat(result.pageEnd()).isEqualTo(6);
            assertThat(result.score()).isEqualTo(0.82);
        });
    }

    private void stubEmptySearch() {
        when(jdbc.query(anyString(), any(RowMapper.class), any(Object[].class))).thenReturn(List.of());
    }

    private QueryInvocation captureQueryInvocation() {
        ArgumentCaptor<String> sql = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<Object[]> params = ArgumentCaptor.forClass(Object[].class);
        verify(jdbc).query(sql.capture(), any(RowMapper.class), params.capture());
        return new QueryInvocation(sql.getValue(), params.getValue());
    }

    private Document document(UUID id, UUID userId, DocumentStatus status) {
        Document document = mock(Document.class);
        lenient().when(document.getId()).thenReturn(id);
        lenient().when(document.getUserId()).thenReturn(userId);
        lenient().when(document.getStatus()).thenReturn(status);
        return document;
    }

    private record QueryInvocation(String sql, Object[] params) {
    }
}
