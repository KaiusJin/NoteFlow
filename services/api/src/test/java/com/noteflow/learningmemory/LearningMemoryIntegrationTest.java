package com.noteflow.learningmemory;

import static org.junit.jupiter.api.Assertions.*;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.workspace.LocalWorkspaceService;
import com.noteflow.documents.DocumentRepository;
import com.noteflow.study.StudyService;
import com.noteflow.tasks.TaskDispatchService;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.mockito.Mockito;

@EnabledIfEnvironmentVariable(named="NOTEFLOW_RUN_DB_TESTS", matches="1")
class LearningMemoryIntegrationTest {
    private JdbcTemplate jdbc;
    private LearningMemoryService memory;
    private AdvancedLearningMemoryService advanced;
    private UUID workspaceId;
    private UUID documentId;
    private TransactionTemplate transactions;

    @BeforeEach void setup() {
        String url = System.getenv().getOrDefault("SPRING_DATASOURCE_URL", "jdbc:postgresql://localhost:5432/noteflow");
        String user = System.getenv().getOrDefault("SPRING_DATASOURCE_USERNAME", "noteflow");
        String password = System.getenv().getOrDefault("SPRING_DATASOURCE_PASSWORD", "noteflow");
        DriverManagerDataSource dataSource=new DriverManagerDataSource(url,user,password);
        jdbc = new JdbcTemplate(dataSource);
        transactions=new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        new LearningMemorySchemaManager(jdbc).run(null);
        workspaceId = UUID.randomUUID(); documentId = UUID.randomUUID();
        jdbc.update("INSERT INTO users(id,display_name,email,created_at,updated_at) VALUES (?,'Learning Memory Test',?,NOW(),NOW())",workspaceId,"learning-memory-"+workspaceId+"@local");
        jdbc.update("INSERT INTO documents(id,user_id,title,storage_path,file_size,status,document_type,content_source_type) VALUES (?,?,?,'/tmp/learning-memory-test.pdf',1,'READY','COURSE_NOTES','TEXT_PDF')",
            documentId,workspaceId,"Learning Memory Test");
        memory = new LearningMemoryService(jdbc, new LocalWorkspaceService(workspaceId), new ObjectMapper());
        advanced = new AdvancedLearningMemoryService(jdbc, new LocalWorkspaceService(workspaceId), memory, new ObjectMapper());
    }

    @AfterEach void cleanup() {
        jdbc.update("DELETE FROM learning_events WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM topic_learning_memory WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM mistake_memory WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_memory_history WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_goals WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_preferences WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_artifact_links WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_topic_edges WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_memory_corrections WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_strategy_experiments WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM learning_study_plans WHERE workspace_id=?", workspaceId);
        jdbc.update("DELETE FROM flashcard_decks WHERE user_id=?",workspaceId);
        jdbc.update("DELETE FROM quiz_sets WHERE user_id=?",workspaceId);
        jdbc.update("DELETE FROM documents WHERE id=?",documentId);
        jdbc.update("DELETE FROM users WHERE id=?",workspaceId);
    }

    @Test void advancedMemoryAndPlannerRoundTrip() {
        memory.record(event("advanced-1", false));
        Map<String,Object> goal = advanced.saveGoal(null,"STAT230 Midterm","Prepare",Instant.now().plusSeconds(86400),
            90,List.of("Covariance"),List.of(documentId));
        advanced.setPreference("answer_detail","concise","EXPLICIT",1);
        for(int i=0;i<4;i++)advanced.setPreference("question_style","short","BEHAVIOR",.6);
        assertEquals(1,advanced.preferences().size());
        advanced.setPreference("question_style","short","BEHAVIOR",.7);
        UUID artifactId=UUID.randomUUID();
        jdbc.update("INSERT INTO quiz_sets(id,document_id,user_id,version,title,difficulty_distribution_json,status) VALUES (?,?,?,1,'Targeted Quiz','{}','READY')",artifactId,documentId,workspaceId);
        advanced.linkArtifact("Covariance","QUIZ",artifactId,"Targeted Quiz",documentId,Map.of());
        advanced.linkTopics("Covariance","Independence","CONFUSED_WITH",.9,"MANUAL");
        assertEquals(1,advanced.goals(false).size());
        assertEquals(2,advanced.preferences().size());
        assertEquals(1,advanced.artifacts("Covariance",20).size());
        assertEquals(1,advanced.topicGraph("Covariance",2).size());
        assertEquals(1,advanced.trend("Covariance",20).size());
        Map<String,Object> corrected=advanced.correct("Covariance",documentId,.8,true,"User correction",1L);
        assertEquals(.8,((Number)corrected.get("mastery")).doubleValue(),.0001);
        Map<String,Object> rebuilt=memory.recalculate("Covariance",documentId);
        assertEquals(.365,((Number)rebuilt.get("mastery")).doubleValue(),.0001);
        Map<String,Object> assignment=advanced.assignExperiment("review-order",List.of("A","B"));
        assertEquals(assignment.get("variant"),advanced.assignExperiment("review-order",List.of("A","B")).get("variant"));
        advanced.experimentOutcome("review-order",.5);
        Map<String,Object> plan=advanced.buildPlan("Today",60);
        assertEquals(goal.get("id"),plan.get("goal_id"));
        org.junit.jupiter.api.Assertions.assertTrue(String.valueOf(plan.get("plan_json")).contains("REUSE_QUIZ"));
        memory.record(new LearningEventRequest("old-note","NOTE_OPENED",Instant.now().minusSeconds(400L*86400),
            List.of("Old Topic"),documentId,"NOTE",UUID.randomUUID(),null,null,null,false,null,null,null,Map.of()));
        assertEquals(1,advanced.expireStale(365));
    }

    @Test void idempotentWritesAndIndexedReadsWorkAgainstPostgres() throws Exception {
        LearningEventRequest wrong = event("same-event", false);
        assertEquals(1, memory.record(wrong).get("acceptedTopics"));
        assertEquals(0, memory.record(wrong).get("acceptedTopics"));

        try (var executor = Executors.newFixedThreadPool(16)) {
            for (int index = 0; index < 100; index++) {
                int value = index;
                executor.submit(() -> memory.record(event("unique-" + value, value % 2 == 0)));
            }
            executor.shutdown();
            if (!executor.awaitTermination(20, TimeUnit.SECONDS)) throw new IllegalStateException("executor timeout");
        }

        Map<String,Object> profile = memory.profile(List.of(documentId), 20);
        assertEquals(1, profile.get("topicCount"));
        Map<?,?> topic = (Map<?,?>) ((List<?>) profile.get("topics")).getFirst();
        assertEquals(101L, ((Number) topic.get("attempts")).longValue());
        assertEquals(101, jdbc.queryForObject("SELECT COUNT(*) FROM learning_events WHERE workspace_id=?", Integer.class, workspaceId));
        assertEquals(1, memory.weakTopics(List.of(documentId), 20).size());
    }

    @Test void firstFailureMetricsExpirationAndStaleCorrectionAreSafe(){
        memory.record(event("first-failure",false));
        Map<String,Object> state=jdbc.queryForMap("SELECT lapse_count,stability_days,calibration_error,is_active,version FROM topic_learning_memory WHERE workspace_id=? AND scope_id=?",workspaceId,documentId);
        assertEquals(1,((Number)state.get("lapse_count")).intValue());
        assertEquals(1d,((Number)state.get("stability_days")).doubleValue(),.0001);
        assertEquals(.1,((Number)state.get("calibration_error")).doubleValue(),.0001);
        advanced.correct("Covariance",documentId,.7,false,"test expiration",1L);
        assertThrows(IllegalStateException.class,()->advanced.correct("Covariance",documentId,.8,true,"stale",1L));
        memory.record(event("reactivate",true));
        assertTrue(jdbc.queryForObject("SELECT is_active FROM topic_learning_memory WHERE workspace_id=? AND scope_id=?",Boolean.class,workspaceId,documentId));
    }

    @Test void recalculateAndLiveWriteUseTheSameTopicLock() throws Exception {
        memory.record(event("before-rebuild",false));
        try(var executor=Executors.newSingleThreadExecutor()){
            var future=transactions.execute(status->{
                jdbc.queryForObject("SELECT pg_advisory_xact_lock(hashtext(?))",Object.class,"learning-memory:"+workspaceId+":"+documentId+":covariance");
                var write=executor.submit(()->transactions.execute(inner->memory.record(event("during-rebuild",true))));
                try{Thread.sleep(100);assertFalse(write.isDone());memory.recalculate("Covariance",documentId);return write;}catch(Exception e){throw new RuntimeException(e);}
            });
            assertNotNull(future); future.get(5,TimeUnit.SECONDS);
        }
        assertEquals(2,jdbc.queryForObject("SELECT attempts FROM topic_learning_memory WHERE workspace_id=? AND scope_id=?",Integer.class,workspaceId,documentId));
        assertEquals(2,jdbc.queryForObject("SELECT COUNT(*) FROM learning_events WHERE workspace_id=? AND scope_id=?",Integer.class,workspaceId,documentId));
    }

    @Test void duplicateFlashcardEventDoesNotAdvanceScheduleTwice(){
        UUID deckId=UUID.randomUUID(),cardId=UUID.randomUUID();
        jdbc.update("INSERT INTO flashcard_decks(id,document_id,user_id,version,title,status,generation_options_json) VALUES (?,?,?,1,'Deck','READY','{}')",deckId,documentId,workspaceId);
        jdbc.update("INSERT INTO flashcards(id,deck_id,document_id,source_group_index,item_index,card_type,front,back,difficulty,topic,source_chunk_ids_json,source_pages_json,dedupe_hash,confidence) VALUES (?,?,?,0,0,'BASIC','Front','Back','MEDIUM','Covariance','[]','[]',?,.9)",cardId,deckId,documentId,"f".repeat(64));
        StudyService study=new StudyService(new LocalWorkspaceService(workspaceId),Mockito.mock(DocumentRepository.class),Mockito.mock(TaskDispatchService.class),jdbc,memory);
        Map<String,Object> first=transactions.execute(status->study.review(cardId,"GOOD","review-once"));
        Map<String,Object> duplicate=transactions.execute(status->study.review(cardId,"GOOD","review-once"));
        assertEquals(1,first.get("repetitions")); assertEquals(1,duplicate.get("repetitions")); assertEquals(true,duplicate.get("duplicate"));
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM learning_events WHERE workspace_id=? AND external_event_id='review-once'",Integer.class,workspaceId));
    }

    private LearningEventRequest event(String id, boolean correct) {
        return new LearningEventRequest(id, "QUESTION_ANSWERED", Instant.now(), List.of("Covariance"), documentId,
            "QUIZ_QUESTION", UUID.randomUUID(), correct, "HARD", 1200, false, null,
            correct ? null : "CONCEPT_CONFUSION", correct ? null : "Zero covariance implies independence", Map.of());
    }
}
