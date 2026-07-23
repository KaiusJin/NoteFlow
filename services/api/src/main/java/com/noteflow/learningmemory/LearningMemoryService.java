package com.noteflow.learningmemory;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.workspace.LocalWorkspaceService;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Timestamp;
import java.text.Normalizer;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class LearningMemoryService {
    private static final Set<String> EVENT_TYPES = Set.of(
        "QUESTION_ANSWERED", "QUIZ_COMPLETED", "FLASHCARD_REVIEWED", "NOTE_OPENED", "NOTE_UPDATED",
        "TOPIC_EXPLAINED", "USER_MARKED_CONFUSED", "USER_MARKED_MASTERED", "STUDY_SESSION_COMPLETED"
    );
    private static final Set<String> FEEDBACK = Set.of("CONFUSED", "MASTERED", "TOO_EASY", "TOO_HARD");
    private static final Set<String> DIFFICULTIES = Set.of("EASY", "MEDIUM", "HARD");
    private static final Set<String> GRADES = Set.of("AGAIN", "HARD", "GOOD", "EASY");

    private final JdbcTemplate jdbc;
    private final LocalWorkspaceService workspaces;
    private final ObjectMapper json;

    public LearningMemoryService(JdbcTemplate jdbc, LocalWorkspaceService workspaces, ObjectMapper json) {
        this.jdbc = jdbc; this.workspaces = workspaces; this.json = json;
    }

    @Transactional
    public Map<String,Object> record(LearningEventRequest request) {
        String eventId = required(request.eventId(), "eventId", 256);
        String eventType = upper(request.eventType());
        if (!EVENT_TYPES.contains(eventType)) throw new IllegalArgumentException("Unsupported learning event type");
        List<String> topics = cleanTopics(request.topics());
        if (topics.isEmpty()) throw new IllegalArgumentException("At least one topic is required");
        if (request.responseTimeMs() != null && request.responseTimeMs() < 0)
            throw new IllegalArgumentException("responseTimeMs cannot be negative");
        String difficulty = request.difficulty() == null ? null : upper(request.difficulty());
        if (difficulty != null && !DIFFICULTIES.contains(difficulty)) throw new IllegalArgumentException("Invalid difficulty");
        String grade = request.reviewGrade() == null ? null : upper(request.reviewGrade());
        if (grade != null && !GRADES.contains(grade)) throw new IllegalArgumentException("Invalid reviewGrade");

        UUID workspaceId = workspaces.currentWorkspaceId();
        validateDocument(request.documentId(),workspaceId);
        UUID scopeId = request.documentId() == null ? workspaceId : request.documentId();
        Instant occurredAt = request.occurredAt() == null ? Instant.now() : request.occurredAt();
        topics.stream().map(LearningMemoryService::topicKey).sorted()
            .forEach(key -> lockTopic(workspaceId, scopeId, key));
        int accepted = 0;
        List<Map<String,Object>> states = new ArrayList<>();
        for (String topic : topics) {
            Update update = updateFor(eventType, request.correct(), difficulty, Boolean.TRUE.equals(request.hintUsed()), grade);
            String topicKey = topicKey(topic);
            UUID rowId = UUID.randomUUID();
            int inserted = jdbc.update("""
                INSERT INTO learning_events(id,workspace_id,scope_id,external_event_id,event_type,topic_key,topic,
                  document_id,artifact_type,artifact_id,correct,difficulty,response_time_ms,hint_used,review_grade,
                  mistake_type,mistake_summary,metadata_json,occurred_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?)
                ON CONFLICT(workspace_id,external_event_id,topic_key) DO NOTHING
                """, rowId, workspaceId, scopeId, eventId, eventType, topicKey, topic, request.documentId(),
                blankToNull(request.artifactType()), request.artifactId(), request.correct(), difficulty,
                request.responseTimeMs(), Boolean.TRUE.equals(request.hintUsed()), grade,
                blankToNull(request.mistakeType()), blankToNull(request.mistakeSummary()), json(request.metadata()),
                Timestamp.from(occurredAt));
            if (inserted == 0) continue;
            accepted++;
            states.add(upsertState(workspaceId, scopeId, topicKey, topic, rowId, occurredAt, request, update));
            updateArtifactInteraction(workspaceId,topicKey,request,occurredAt);
            observePracticePreference(workspaceId,topicKey,eventType,eventId);
            if (Boolean.FALSE.equals(request.correct()) || "USER_MARKED_CONFUSED".equals(eventType))
                upsertMistake(workspaceId, scopeId, topicKey, topic, rowId, occurredAt, request);
        }
        Map<String,Object> result = new LinkedHashMap<>();
        result.put("eventId", eventId); result.put("acceptedTopics", accepted);
        result.put("duplicateTopics", topics.size() - accepted); result.put("states", states);
        return result;
    }

    @Transactional
    public Map<String,Object> recordFeedback(LearningFeedbackRequest request) {
        String feedback = upper(request.feedback());
        if (!FEEDBACK.contains(feedback)) throw new IllegalArgumentException("Unsupported feedback");
        String eventType = "MASTERED".equals(feedback) ? "USER_MARKED_MASTERED" : "USER_MARKED_CONFUSED";
        if (Set.of("TOO_EASY", "TOO_HARD").contains(feedback)) {
            eventType = "TOPIC_EXPLAINED";
            setExplicitPreference("topic_difficulty:"+sha256(topicKey(required(request.topic(),"topic",1000))).substring(0,64),
                "TOO_EASY".equals(feedback)?"HARDER":"EASIER");
        }
        return record(new LearningEventRequest(
            required(request.eventId(), "eventId", 256), eventType, Instant.now(), List.of(request.topic()),
            request.documentId(), "USER_FEEDBACK", null, "MASTERED".equals(feedback) ? Boolean.TRUE :
            "CONFUSED".equals(feedback) ? Boolean.FALSE : null, null, null, false, null,
            blankToNull(request.mistakeType()), blankToNull(request.detail()), Map.of("feedback", feedback)
        ));
    }

    /** Emits one idempotent event per graded answer. Safe to call after retries. */
    @Transactional
    public int recordQuizAttempt(UUID attemptId) {
        UUID workspaceId = workspaces.currentWorkspaceId();
        List<Map<String,Object>> answers = jdbc.queryForList("""
            SELECT ans.id,ans.is_correct,ans.user_response,ans.response_time_ms,ans.hint_used,q.topic,q.difficulty,q.common_mistake,
                   q.id question_id,s.id quiz_set_id,s.document_id
              FROM quiz_answers ans
              JOIN quiz_questions q ON q.id=ans.question_id
              JOIN quiz_attempts a ON a.id=ans.attempt_id
              JOIN quiz_sets s ON s.id=a.quiz_set_id
             WHERE a.id=? AND a.user_id=? AND ans.graded_by IS NOT NULL
            """, attemptId, workspaceId);
        int accepted = 0;
        for (Map<String,Object> answer : answers) {
            Map<String,Object> result = record(new LearningEventRequest(
                "quiz-answer:" + answer.get("id") + ":v1", "QUESTION_ANSWERED", Instant.now(),
                List.of(String.valueOf(answer.get("topic"))), (UUID) answer.get("document_id"), "QUIZ",
                (UUID) answer.get("quiz_set_id"), (Boolean) answer.get("is_correct"),
                String.valueOf(answer.get("difficulty")), (Integer)answer.get("response_time_ms"), Boolean.TRUE.equals(answer.get("hint_used")), null,
                Boolean.FALSE.equals(answer.get("is_correct")) ? "UNCLASSIFIED" : null,
                Boolean.FALSE.equals(answer.get("is_correct")) ? blankToNull((String) answer.get("common_mistake")) : null,
                Map.of("attemptId", attemptId.toString(),"questionId",String.valueOf(answer.get("question_id")))
            ));
            accepted += ((Number) result.get("acceptedTopics")).intValue();
        }
        return accepted;
    }

    public Map<String,Object> profile(List<UUID> documentIds, int limit) {
        List<Map<String,Object>> topics = topicRows(documentIds, clampLimit(limit, 200), null,
            "mastery ASC, incorrect_count DESC, last_activity_at DESC");
        Map<String,Object> result = new LinkedHashMap<>();
        result.put("topicCount", topics.size());
        result.put("averageMastery", topics.stream().mapToDouble(r -> ((Number) r.get("mastery")).doubleValue()).average().orElse(0));
        result.put("topics", topics);
        result.put("weakTopics", weakTopics(documentIds, Math.min(10, clampLimit(limit, 200))));
        result.put("dueReviews", dueReviews(documentIds, Math.min(10, clampLimit(limit, 200))));
        return result;
    }

    /** Records bounded document activity only for topics already mapped to the document. */
    @Transactional public int recordDocumentActivity(UUID documentId,String eventType,String eventId){
        UUID workspaceId=workspaces.currentWorkspaceId(); validateDocument(documentId,workspaceId);
        String type=upper(eventType);
        if(!Set.of("NOTE_OPENED","NOTE_UPDATED").contains(type))throw new IllegalArgumentException("Invalid document activity type");
        List<String> topics=jdbc.queryForList("SELECT topic FROM topic_learning_memory WHERE workspace_id=? AND scope_id=? AND is_active ORDER BY last_activity_at DESC LIMIT 20",String.class,workspaceId,documentId);
        if(topics.isEmpty())return 0;
        Map<String,Object> result=record(new LearningEventRequest(eventId,type,Instant.now(),topics,documentId,"NOTE",documentId,
            null,null,null,false,null,null,null,Map.of()));
        return ((Number)result.get("acceptedTopics")).intValue();
    }

    public List<Map<String,Object>> weakTopics(List<UUID> documentIds, int limit) {
        List<Map<String,Object>> rows = topicRows(documentIds, clampLimit(limit, 100),
            "(mastery < 0.75 OR needs_review)", "needs_review DESC, mastery ASC, incorrect_count DESC, last_activity_at DESC");
        attachMistakes(rows, documentIds);
        for (Map<String,Object> row : rows) {
            double mastery = ((Number) row.get("mastery")).doubleValue();
            int incorrect = ((Number) row.get("incorrect_count")).intValue();
            row.put("weakness", Math.round((1 - mastery) * 10000d) / 10000d);
            int attempts=((Number)row.get("attempts")).intValue(),hints=((Number)row.get("hint_count")).intValue();
            double averageMs=((Number)row.get("average_response_time_ms")).doubleValue();
            List<String> reasons=new ArrayList<>();if(incorrect>0)reasons.add(incorrect+" incorrect answer(s)");
            if(attempts>=3&&((Number)row.get("easy_attempts")).intValue()==attempts)reasons.add("evidence is limited to easy questions");
            if(attempts>=3&&hints*1d/attempts>=.4)reasons.add("high hint dependence");if(averageMs>120000)reasons.add("unusually slow responses");
            if(((Number)row.get("recent_trend")).doubleValue()<-.25)reasons.add("declining recent performance");
            if(reasons.isEmpty())reasons.add("review is due");row.put("reason",String.join("; ",reasons));
        }
        return rows;
    }

    public List<Map<String,Object>> dueReviews(List<UUID> documentIds, int limit) {
        return topicRows(documentIds, clampLimit(limit, 100),
            "needs_review AND next_review_at IS NOT NULL AND next_review_at <= NOW()",
            "next_review_at ASC, mastery ASC");
    }

    public Map<String,Object> explain(String topic, List<UUID> documentIds) {
        String key = topicKey(required(topic, "topic", 1000));
        List<Map<String,Object>> rows = topicRows(documentIds, 1, "topic_key='" + key.replace("'", "''") + "'", "last_activity_at DESC");
        if (rows.isEmpty()) throw new IllegalArgumentException("No learning memory exists for this topic");
        attachMistakes(rows, documentIds);
        Map<String,Object> explanation = new LinkedHashMap<>(rows.get(0));
        explanation.put("explanation", String.format(Locale.ROOT,
            "Mastery %.0f%% from %d attempts (%d correct, %d incorrect).",
            ((Number) explanation.get("mastery")).doubleValue() * 100,
            ((Number) explanation.get("attempts")).intValue(),
            ((Number) explanation.get("correct_count")).intValue(),
            ((Number) explanation.get("incorrect_count")).intValue()));
        return explanation;
    }

    /** Rebuild one derived topic from immutable raw events after an algorithm upgrade. */
    @Transactional public Map<String,Object> recalculate(String topic,UUID requestedScopeId){
        UUID workspaceId=workspaces.currentWorkspaceId(),scopeId=requestedScopeId==null?workspaceId:requestedScopeId;
        String key=topicKey(required(topic,"topic",1000));
        lockTopic(workspaceId,scopeId,key);
        List<Map<String,Object>> events=jdbc.queryForList("SELECT * FROM learning_events WHERE workspace_id=? AND scope_id=? AND topic_key=? ORDER BY occurred_at,created_at,id",workspaceId,scopeId,key);
        if(events.isEmpty())throw new IllegalArgumentException("No raw events exist for this topic");
        jdbc.update("DELETE FROM learning_memory_history WHERE workspace_id=? AND scope_id=? AND topic_key=?",workspaceId,scopeId,key);
        jdbc.update("DELETE FROM mistake_memory WHERE workspace_id=? AND scope_id=? AND topic_key=?",workspaceId,scopeId,key);
        jdbc.update("DELETE FROM topic_learning_memory WHERE workspace_id=? AND scope_id=? AND topic_key=?",workspaceId,scopeId,key);
        Map<String,Object> state=Map.of();
        for(Map<String,Object> row:events){
            String type=String.valueOf(row.get("event_type"));
            LearningEventRequest event=new LearningEventRequest(String.valueOf(row.get("external_event_id")),type,
                ((Timestamp)row.get("occurred_at")).toInstant(),List.of(String.valueOf(row.get("topic"))),
                (UUID)row.get("document_id"),(String)row.get("artifact_type"),(UUID)row.get("artifact_id"),
                (Boolean)row.get("correct"),(String)row.get("difficulty"),(Integer)row.get("response_time_ms"),
                (Boolean)row.get("hint_used"),(String)row.get("review_grade"),(String)row.get("mistake_type"),
                (String)row.get("mistake_summary"),Map.of());
            Update update=updateFor(type,event.correct(),event.difficulty(),Boolean.TRUE.equals(event.hintUsed()),event.reviewGrade());
            UUID eventId=(UUID)row.get("id");
            state=upsertState(workspaceId,scopeId,key,event.topics().getFirst(),eventId,event.occurredAt(),event,update);
            if(Boolean.FALSE.equals(event.correct())||"USER_MARKED_CONFUSED".equals(type))
                upsertMistake(workspaceId,scopeId,key,event.topics().getFirst(),eventId,event.occurredAt(),event);
        }
        return state;
    }

    private Map<String,Object> upsertState(UUID workspaceId, UUID scopeId, String key, String topic, UUID eventId, Instant at,
                                           LearningEventRequest event, Update update) {
        boolean attempt = Set.of("QUESTION_ANSWERED", "FLASHCARD_REVIEWED").contains(upper(event.eventType()));
        int correct = attempt && Boolean.TRUE.equals(update.correct) ? 1 : 0;
        int incorrect = attempt && Boolean.FALSE.equals(update.correct) ? 1 : 0;
        int hints = Boolean.TRUE.equals(event.hintUsed()) ? 1 : 0;
        int responseMs = event.responseTimeMs() == null ? 0 : event.responseTimeMs();
        int responseCount=attempt&&event.responseTimeMs()!=null?1:0;
        int easy=attempt&&"EASY".equals(upper(event.difficulty()))?1:0;
        int hard=attempt&&"HARD".equals(upper(event.difficulty()))?1:0;
        int medium=(attempt?1:0)-easy-hard;
        Instant nextReview = at.plus(update.reviewDays, ChronoUnit.DAYS);
        double initialStability = incorrect > 0 ? 1 : correct > 0 ? Math.min(365, 1 + Math.max(1, update.weight * 2)) : 1;
        double initialCalibration = attempt ? .1 : 0;
        double initialConfidence=Math.max(0,Math.min(1,update.weight*.08)-(attempt?.02:0));
        Map<String,Object> state = jdbc.queryForMap("""
            INSERT INTO topic_learning_memory(workspace_id,scope_id,topic_key,topic,mastery,confidence,evidence_weight,
              attempts,correct_count,incorrect_count,hint_count,total_response_time_ms,response_time_count,consecutive_correct,
              consecutive_incorrect,recent_trend,last_activity_at,last_reviewed_at,next_review_at,needs_review,
              lapse_count,stability_days,calibration_error,is_active,easy_attempts,medium_attempts,hard_attempts)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(workspace_id,scope_id,topic_key) DO UPDATE SET
              topic=EXCLUDED.topic,
              is_active=TRUE,
              mastery=GREATEST(0,LEAST(1,topic_learning_memory.mastery+?)),
              confidence=GREATEST(0,LEAST(1,topic_learning_memory.confidence+?-CASE WHEN EXCLUDED.attempts>0 THEN
                ABS(topic_learning_memory.mastery-CASE WHEN EXCLUDED.correct_count>0 THEN 1 ELSE 0 END)*.04 ELSE 0 END)),
              evidence_weight=topic_learning_memory.evidence_weight+EXCLUDED.evidence_weight,
              attempts=topic_learning_memory.attempts+EXCLUDED.attempts,
              correct_count=topic_learning_memory.correct_count+EXCLUDED.correct_count,
              incorrect_count=topic_learning_memory.incorrect_count+EXCLUDED.incorrect_count,
              easy_attempts=topic_learning_memory.easy_attempts+EXCLUDED.easy_attempts,
              medium_attempts=topic_learning_memory.medium_attempts+EXCLUDED.medium_attempts,
              hard_attempts=topic_learning_memory.hard_attempts+EXCLUDED.hard_attempts,
              hint_count=topic_learning_memory.hint_count+EXCLUDED.hint_count,
              total_response_time_ms=topic_learning_memory.total_response_time_ms+EXCLUDED.total_response_time_ms,
              response_time_count=topic_learning_memory.response_time_count+EXCLUDED.response_time_count,
              consecutive_correct=CASE WHEN EXCLUDED.correct_count>0 THEN topic_learning_memory.consecutive_correct+1
                                       WHEN EXCLUDED.incorrect_count>0 THEN 0 ELSE topic_learning_memory.consecutive_correct END,
              consecutive_incorrect=CASE WHEN EXCLUDED.incorrect_count>0 THEN topic_learning_memory.consecutive_incorrect+1
                                         WHEN EXCLUDED.correct_count>0 THEN 0 ELSE topic_learning_memory.consecutive_incorrect END,
              recent_trend=topic_learning_memory.recent_trend*0.7+EXCLUDED.recent_trend*0.3,
              lapse_count=topic_learning_memory.lapse_count+EXCLUDED.incorrect_count,
              stability_days=CASE WHEN EXCLUDED.incorrect_count>0 THEN GREATEST(1,topic_learning_memory.stability_days*.55)
                WHEN EXCLUDED.correct_count>0 THEN LEAST(365,topic_learning_memory.stability_days+GREATEST(1,EXCLUDED.evidence_weight*2))
                ELSE topic_learning_memory.stability_days END,
              calibration_error=CASE WHEN EXCLUDED.attempts>0 THEN topic_learning_memory.calibration_error*.8+
                ABS(topic_learning_memory.mastery-CASE WHEN EXCLUDED.correct_count>0 THEN 1 ELSE 0 END)*.2
                ELSE topic_learning_memory.calibration_error END,
              last_activity_at=GREATEST(topic_learning_memory.last_activity_at,EXCLUDED.last_activity_at),
              last_reviewed_at=CASE WHEN EXCLUDED.last_reviewed_at IS NULL THEN topic_learning_memory.last_reviewed_at
                ELSE GREATEST(topic_learning_memory.last_reviewed_at,EXCLUDED.last_reviewed_at) END,
              next_review_at=CASE WHEN EXCLUDED.last_activity_at>=topic_learning_memory.last_activity_at
                THEN CASE WHEN EXCLUDED.correct_count>0 THEN EXCLUDED.last_activity_at+
                  make_interval(days=>CEIL(LEAST(365,topic_learning_memory.stability_days+GREATEST(1,EXCLUDED.evidence_weight*2)))::integer)
                  ELSE EXCLUDED.next_review_at END ELSE topic_learning_memory.next_review_at END,
              needs_review=(GREATEST(0,LEAST(1,topic_learning_memory.mastery+?))<0.7 OR EXCLUDED.incorrect_count>0),
              version=topic_learning_memory.version+1,updated_at=NOW()
            RETURNING topic,mastery,confidence,attempts,correct_count,incorrect_count,recent_trend,next_review_at,needs_review,version
            """, workspaceId, scopeId, key, topic, clamp(.5 + update.delta), initialConfidence, update.weight,
            attempt ? 1 : 0, correct, incorrect, hints, responseMs,responseCount, correct, incorrect, update.signal,
            Timestamp.from(at), update.reviewEvidence ? Timestamp.from(at) : null, Timestamp.from(nextReview),
            update.needsReview || clamp(.5 + update.delta) < .7,incorrect,initialStability,initialCalibration,true,easy,medium,hard,
            update.delta, update.weight * .08, update.delta);
        jdbc.update("""
          INSERT INTO learning_memory_history(id,workspace_id,scope_id,topic_key,source_event_id,
          mastery,confidence,attempts,recent_trend,algorithm_version,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,'v1',?)""",
          UUID.randomUUID(),workspaceId,scopeId,key,eventId,state.get("mastery"),state.get("confidence"),
          state.get("attempts"),state.get("recent_trend"),Timestamp.from(at));
        return state;
    }

    private void upsertMistake(UUID workspaceId, UUID scopeId, String key, String topic, UUID eventId, Instant at,
                               LearningEventRequest request) {
        String type = Optional.ofNullable(blankToNull(request.mistakeType())).orElse("UNCLASSIFIED").toUpperCase(Locale.ROOT);
        String summary = Optional.ofNullable(blankToNull(request.mistakeSummary())).orElse("Incorrect answer or explicit confusion");
        String fingerprint = sha256(type + "\n" + summary.toLowerCase(Locale.ROOT));
        jdbc.update("""
            INSERT INTO mistake_memory(workspace_id,scope_id,topic_key,mistake_fingerprint,topic,mistake_type,summary,
              first_seen_at,last_seen_at,last_event_id) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(workspace_id,scope_id,topic_key,mistake_fingerprint) DO UPDATE SET
              occurrences=mistake_memory.occurrences+1,last_seen_at=GREATEST(mistake_memory.last_seen_at,EXCLUDED.last_seen_at),
              last_event_id=EXCLUDED.last_event_id,summary=EXCLUDED.summary,version=mistake_memory.version+1
            """, workspaceId, scopeId, key, fingerprint, topic, type, summary, Timestamp.from(at), Timestamp.from(at), eventId);
    }

    private void updateArtifactInteraction(UUID workspaceId,String key,LearningEventRequest request,Instant at){
        if(request.artifactId()==null)return;
        String type=upper(request.artifactType());
        if("FLASHCARD".equals(type))type="FLASHCARDS";
        if("QUIZ_QUESTION".equals(type))type="QUIZ";
        jdbc.update("UPDATE learning_artifact_links SET interaction_count=interaction_count+1,last_interacted_at=GREATEST(COALESCE(last_interacted_at,?::timestamptz),?::timestamptz),updated_at=NOW() WHERE workspace_id=? AND topic_key=? AND artifact_type=? AND artifact_id=?",
            Timestamp.from(at),Timestamp.from(at),workspaceId,key,type,request.artifactId());
    }

    private void observePracticePreference(UUID workspaceId,String topicKey,String eventType,String eventId){
        String format="QUESTION_ANSWERED".equals(eventType)?"QUIZ":"FLASHCARD_REVIEWED".equals(eventType)?"FLASHCARDS":null;
        if(format==null||Long.parseUnsignedLong(sha256(eventId).substring(0,8),16)%5!=0)return;
        String value=jsonValue(format);
        jdbc.update("""
          INSERT INTO learning_preferences(workspace_id,preference_key,value_json,source,confidence,evidence_count)
          VALUES (?,?,?::jsonb,'BEHAVIOR',.1,1)
          ON CONFLICT(workspace_id,preference_key) DO UPDATE SET
          value_json=CASE WHEN learning_preferences.source='EXPLICIT' THEN learning_preferences.value_json ELSE EXCLUDED.value_json END,
          evidence_count=CASE WHEN learning_preferences.source='EXPLICIT' THEN learning_preferences.evidence_count
            WHEN learning_preferences.value_json=EXCLUDED.value_json THEN learning_preferences.evidence_count+1 ELSE 1 END,
          confidence=CASE WHEN learning_preferences.source='EXPLICIT' THEN 1
            WHEN learning_preferences.value_json=EXCLUDED.value_json THEN LEAST(.95,(learning_preferences.evidence_count+1)/10.0) ELSE .1 END,
          version=learning_preferences.version+1,updated_at=NOW()
          """,workspaceId,"practice_format_topic:"+sha256(topicKey).substring(0,64),value);
    }

    private void setExplicitPreference(String key,Object value){
        jdbc.update("""
          INSERT INTO learning_preferences(workspace_id,preference_key,value_json,source,confidence,evidence_count)
          VALUES (?,?,?::jsonb,'EXPLICIT',1,1)
          ON CONFLICT(workspace_id,preference_key) DO UPDATE SET value_json=EXCLUDED.value_json,source='EXPLICIT',
          confidence=1,evidence_count=learning_preferences.evidence_count+1,version=learning_preferences.version+1,updated_at=NOW()
          """,workspaces.currentWorkspaceId(),key,jsonValue(value));
    }

    private List<Map<String,Object>> topicRows(List<UUID> documents, int limit, String extra, String order) {
        List<Object> args = new ArrayList<>(); args.add(workspaces.currentWorkspaceId());
        String scope = scopeClause(documents, args);
        String filter = extra == null ? "" : " AND " + extra;
        args.add(limit);
        String sql = """
            SELECT topic_key,MAX(topic) topic,
              SUM(mastery*GREATEST(evidence_weight,0.1))/SUM(GREATEST(evidence_weight,0.1)) mastery,
              MAX(confidence) confidence,SUM(evidence_weight) evidence_weight,SUM(attempts) attempts,
              SUM(correct_count) correct_count,SUM(incorrect_count) incorrect_count,SUM(hint_count) hint_count,
              SUM(easy_attempts) easy_attempts,SUM(medium_attempts) medium_attempts,SUM(hard_attempts) hard_attempts,
              CASE WHEN SUM(response_time_count)>0 THEN SUM(total_response_time_ms)::double precision/SUM(response_time_count) ELSE 0 END average_response_time_ms,
              AVG(recent_trend) recent_trend,MAX(last_activity_at) last_activity_at,MAX(last_reviewed_at) last_reviewed_at,
              AVG(stability_days) stability_days,AVG(calibration_error) calibration_error,SUM(lapse_count) lapse_count,
              MIN(next_review_at) next_review_at,BOOL_OR(needs_review) needs_review,MAX(version) version
            FROM topic_learning_memory WHERE workspace_id=? AND is_active %s %s GROUP BY topic_key ORDER BY %s LIMIT ?
            """.formatted(scope, filter, order);
        return new ArrayList<>(jdbc.queryForList(sql, args.toArray()));
    }

    private void attachMistakes(List<Map<String,Object>> rows, List<UUID> documents) {
        if (rows.isEmpty()) return;
        List<Object> args = new ArrayList<>(); args.add(workspaces.currentWorkspaceId());
        String scope = scopeClause(documents, args);
        String placeholders = String.join(",", Collections.nCopies(rows.size(), "?"));
        rows.forEach(row -> args.add(row.get("topic_key")));
        List<Map<String,Object>> mistakes = jdbc.queryForList("SELECT topic_key,mistake_type,summary,SUM(occurrences) occurrences,MAX(last_seen_at) last_seen_at " +
            "FROM mistake_memory WHERE workspace_id=? " + scope + " AND topic_key IN (" + placeholders + ") " +
            "GROUP BY topic_key,mistake_type,summary ORDER BY occurrences DESC,last_seen_at DESC", args.toArray());
        Map<String,List<Map<String,Object>>> byTopic = new HashMap<>();
        for (Map<String,Object> mistake : mistakes)
            byTopic.computeIfAbsent(String.valueOf(mistake.get("topic_key")), ignored -> new ArrayList<>()).add(mistake);
        rows.forEach(row -> row.put("mistakes", byTopic.getOrDefault(String.valueOf(row.get("topic_key")), List.of()).stream().limit(3).toList()));
    }

    private String scopeClause(List<UUID> documents, List<Object> args) {
        if (documents == null || documents.isEmpty()) return "";
        List<UUID> unique = documents.stream().filter(Objects::nonNull).distinct().limit(100).toList();
        if (unique.isEmpty()) return "";
        args.addAll(unique);
        return " AND scope_id IN (" + String.join(",", Collections.nCopies(unique.size(), "?")) + ")";
    }

    private Update updateFor(String type, Boolean correct, String difficulty, boolean hint, String grade) {
        double multiplier = switch (difficulty == null ? "MEDIUM" : difficulty) { case "EASY" -> .75; case "HARD" -> 1.35; default -> 1; };
        if ("USER_MARKED_MASTERED".equals(type)) return new Update(.20, 2, 1, true, false, 14, true);
        if ("USER_MARKED_CONFUSED".equals(type)) return new Update(-.16, 2, -1, false, true, 1, true);
        if ("FLASHCARD_REVIEWED".equals(type)) {
            return switch (grade == null ? "GOOD" : grade) {
                case "AGAIN" -> new Update(-.08, 1, -1, false, true, 1, true);
                case "HARD" -> new Update(.012, .8, .2, true, false, 2, true);
                case "EASY" -> new Update(.055, 1.2, 1, true, false, 10, true);
                default -> new Update(.035, 1, .7, true, false, 5, true);
            };
        }
        if ("QUESTION_ANSWERED".equals(type)) {
            boolean ok = Boolean.TRUE.equals(correct);
            double delta = (ok ? .06 : -.10) * multiplier * (hint && ok ? .55 : 1);
            return new Update(delta, multiplier * (hint ? .65 : 1), ok ? 1 : -1, ok, !ok, ok ? 5 : 1, true);
        }
        return new Update(.005, .15, .05, null, false, 3, false);
    }

    private List<String> cleanTopics(List<String> values) {
        if (values == null) return List.of();
        LinkedHashMap<String,String> result = new LinkedHashMap<>();
        for (String value : values) {
            String topic = required(value, "topic", 1000).replaceAll("\\s+", " ");
            result.putIfAbsent(topicKey(topic), topic);
            if (result.size() > 20) throw new IllegalArgumentException("At most 20 topics are allowed per event");
        }
        return List.copyOf(result.values());
    }

    private String json(Map<String,Object> value) {
        try { return json.writeValueAsString(value == null ? Map.of() : value); }
        catch (JsonProcessingException e) { throw new IllegalArgumentException("metadata is not JSON serializable", e); }
    }
    private String jsonValue(Object value){try{return json.writeValueAsString(value);}catch(JsonProcessingException e){throw new IllegalArgumentException("value is not JSON serializable",e);}}
    private static String topicKey(String value) { return Normalizer.normalize(value, Normalizer.Form.NFKC).trim().replaceAll("\\s+", " ").toLowerCase(Locale.ROOT); }
    private void lockTopic(UUID workspaceId,UUID scopeId,String key){
        jdbc.queryForObject("SELECT pg_advisory_xact_lock(hashtext(?))",Object.class,
            "learning-memory:"+workspaceId+":"+scopeId+":"+key);
    }
    private void validateDocument(UUID documentId,UUID workspaceId){
        if(documentId==null)return;
        Integer count=jdbc.queryForObject("SELECT COUNT(*) FROM documents WHERE id=? AND user_id=?",Integer.class,documentId,workspaceId);
        if(count==null||count!=1)throw new IllegalArgumentException("Document not found");
    }
    private static String required(String value, String name, int max) {
        String clean = blankToNull(value); if (clean == null) throw new IllegalArgumentException(name + " is required");
        if (clean.length() > max) throw new IllegalArgumentException(name + " is too long"); return clean;
    }
    private static String blankToNull(String value) { return value == null || value.trim().isEmpty() ? null : value.trim(); }
    private static String upper(String value) { return value == null ? "" : value.trim().toUpperCase(Locale.ROOT); }
    private static double clamp(double value) { return Math.max(0, Math.min(1, value)); }
    private static int clampLimit(int value, int max) { return Math.max(1, Math.min(max, value)); }
    private static String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
    private record Update(double delta, double weight, double signal, Boolean correct, boolean needsReview,
                          int reviewDays, boolean reviewEvidence) {}
}
