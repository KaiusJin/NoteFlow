package com.noteflow.study;

import com.noteflow.documents.*;
import com.noteflow.learningmemory.LearningEventRequest;
import com.noteflow.learningmemory.LearningMemoryService;
import com.noteflow.tasks.*;
import com.noteflow.workspace.LocalWorkspaceService;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class QuizStudyService {
    private final LocalWorkspaceService users;
    private final DocumentRepository documents;
    private final TaskDispatchService tasks;
    private final JdbcTemplate jdbc;
    private final LearningMemoryService memory;

    public QuizStudyService(LocalWorkspaceService users, DocumentRepository documents, TaskDispatchService tasks, JdbcTemplate jdbc, LearningMemoryService memory) {
        this.users = users;
        this.documents = documents;
        this.tasks = tasks;
        this.jdbc = jdbc;
        this.memory = memory;
    }

    public List<Map<String,Object>> quizzes(UUID id){UUID u=users.currentUserId();owned(id,u);return jdbc.queryForList("SELECT id,version,title,status,origin,source_scope_json,generation_options_json,total_source_groups,completed_source_groups,quality_report_json,error_message,created_at FROM quiz_sets WHERE document_id=? AND user_id=? ORDER BY version DESC",id,u);}

    public List<Map<String,Object>> questions(UUID id){UUID u=users.currentUserId();quiz(id,u);return jdbc.queryForList("SELECT id,question_type,difficulty,topic,stem,options_json,points,source_pages_json FROM quiz_questions WHERE quiz_set_id=? ORDER BY source_group_index,item_index",id);}

    @Transactional public Map<String,Object> startAttempt(UUID setId){UUID u=users.currentUserId();quiz(setId,u);UUID id=UUID.randomUUID();jdbc.update("INSERT INTO quiz_attempts(id,quiz_set_id,user_id,status) VALUES (?,?,?,'IN_PROGRESS')",id,setId,u);return Map.of("attemptId",id,"status","IN_PROGRESS");}

    @Transactional public Map<String,Object> saveAnswer(UUID attemptId,UUID questionId,String response,Integer responseTimeMs,boolean hintUsed){UUID u=users.currentUserId();attempt(attemptId,u,"IN_PROGRESS");if(responseTimeMs!=null&&responseTimeMs<0)throw new IllegalArgumentException("responseTimeMs cannot be negative");int valid=jdbc.queryForObject("SELECT COUNT(*) FROM quiz_questions q JOIN quiz_attempts a ON a.quiz_set_id=q.quiz_set_id WHERE a.id=? AND q.id=?",Integer.class,attemptId,questionId);if(valid==0)throw new IllegalArgumentException("Question is not part of attempt");jdbc.update("INSERT INTO quiz_answers(id,attempt_id,question_id,user_response,response_time_ms,hint_used) VALUES (?,?,?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET user_response=EXCLUDED.user_response,response_time_ms=EXCLUDED.response_time_ms,hint_used=EXCLUDED.hint_used,is_correct=NULL,awarded_points=NULL,feedback=NULL,key_points_hit_json=NULL,graded_by=NULL,updated_at=NOW()",UUID.randomUUID(),attemptId,questionId,response==null?"":response,responseTimeMs,hintUsed);return Map.of("saved",true);}

    @Transactional
    public Map<String,Object> saveAnswers(UUID attemptId,List<StudyController.BatchAnswerRequest> answers) {
        UUID userId=users.currentUserId();
        attempt(attemptId,userId,"IN_PROGRESS");
        if(answers==null||answers.isEmpty())return Map.of("saved",0);
        if(answers.size()>500)throw new IllegalArgumentException("At most 500 answers can be saved at once");
        Set<UUID> allowed=new HashSet<>(jdbc.queryForList(
            "SELECT q.id FROM quiz_questions q JOIN quiz_attempts a ON a.quiz_set_id=q.quiz_set_id WHERE a.id=?",
            UUID.class,attemptId));
        Set<UUID> seen=new HashSet<>();
        List<Object[]> batch=new ArrayList<>(answers.size());
        for(var answer:answers){
            if(answer==null||answer.questionId()==null||!allowed.contains(answer.questionId()))
                throw new IllegalArgumentException("Question is not part of attempt");
            if(!seen.add(answer.questionId()))throw new IllegalArgumentException("Duplicate question in answer batch");
            if(answer.responseTimeMs()!=null&&answer.responseTimeMs()<0)
                throw new IllegalArgumentException("responseTimeMs cannot be negative");
            batch.add(new Object[]{UUID.randomUUID(),attemptId,answer.questionId(),
                answer.response()==null?"":answer.response(),answer.responseTimeMs(),Boolean.TRUE.equals(answer.hintUsed())});
        }
        jdbc.batchUpdate("INSERT INTO quiz_answers(id,attempt_id,question_id,user_response,response_time_ms,hint_used) VALUES (?,?,?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET user_response=EXCLUDED.user_response,response_time_ms=EXCLUDED.response_time_ms,hint_used=EXCLUDED.hint_used,is_correct=NULL,awarded_points=NULL,feedback=NULL,key_points_hit_json=NULL,graded_by=NULL,updated_at=NOW()",batch);
        return Map.of("saved",batch.size());
    }

    @Transactional public Map<String,Object> submit(UUID attemptId){UUID u=users.currentUserId();attempt(attemptId,u,"IN_PROGRESS");UUID documentId=jdbc.queryForObject("SELECT s.document_id FROM quiz_attempts a JOIN quiz_sets s ON s.id=a.quiz_set_id WHERE a.id=?",UUID.class,attemptId);jdbc.update("INSERT INTO quiz_answers(id,attempt_id,question_id,user_response) SELECT gen_random_uuid(),?,q.id,'' FROM quiz_questions q JOIN quiz_attempts a ON a.quiz_set_id=q.quiz_set_id WHERE a.id=? ON CONFLICT(attempt_id,question_id) DO NOTHING",attemptId,attemptId);for(var r:jdbc.queryForList("SELECT a.id,q.correct_answer,a.user_response,q.points,q.explanation FROM quiz_answers a JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=? AND q.question_type IN ('MULTIPLE_CHOICE','TRUE_FALSE')",attemptId)){boolean ok=norm((String)r.get("correct_answer")).equals(norm((String)r.get("user_response")));jdbc.update("UPDATE quiz_answers SET is_correct=?,awarded_points=?,feedback=?,key_points_hit_json='[]',graded_by='AUTO',updated_at=NOW() WHERE id=?",ok,ok?((Number)r.get("points")).doubleValue():0d,r.get("explanation"),r.get("id"));}int free=jdbc.queryForObject("SELECT COUNT(*) FROM quiz_answers a JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=? AND q.question_type NOT IN ('MULTIPLE_CHOICE','TRUE_FALSE')",Integer.class,attemptId);if(free>0){jdbc.update("UPDATE quiz_attempts SET status='GRADING',submitted_at=NOW(),updated_at=NOW() WHERE id=?",attemptId);Task t=tasks.createAndEnqueue(documentId,u,TaskType.GRADE_QUIZ_ATTEMPT,attemptId);return Map.of("attemptId",attemptId,"taskId",t.getId(),"status","GRADING");}jdbc.update("UPDATE quiz_attempts a SET status='COMPLETED',score=x.score,max_score=x.maximum,submitted_at=NOW(),completed_at=NOW(),updated_at=NOW() FROM (SELECT COALESCE(SUM(ans.awarded_points),0) score,COALESCE(SUM(q.points),0) maximum FROM quiz_answers ans JOIN quiz_questions q ON q.id=ans.question_id WHERE ans.attempt_id=?) x WHERE a.id=?",attemptId,attemptId);memory.recordQuizAttempt(attemptId);return Map.of("attemptId",attemptId,"status","COMPLETED");}

    public Map<String,Object> attemptResult(UUID id){UUID u=users.currentUserId();attempt(id,u,null);Map<String,Object> meta=jdbc.queryForMap("SELECT a.id,a.quiz_set_id,a.status,a.score,a.max_score,a.weak_topics_json,a.started_at,a.submitted_at,a.completed_at,s.generation_options_json FROM quiz_attempts a JOIN quiz_sets s ON s.id=a.quiz_set_id WHERE a.id=?",id);var answers=jdbc.queryForList("SELECT ans.question_id,ans.user_response,ans.is_correct,ans.awarded_points,ans.feedback,ans.graded_by,q.stem,q.question_type,q.difficulty,q.topic,q.options_json,q.correct_answer,q.explanation,q.points FROM quiz_answers ans JOIN quiz_questions q ON q.id=ans.question_id WHERE ans.attempt_id=? ORDER BY q.source_group_index,q.item_index",id);return Map.of("attempt",meta,"answers",answers);}

    private String norm(String value) {
        return value == null ? "" : value.trim().toUpperCase(Locale.ROOT);
    }

    private void quiz(UUID id, UUID userId) {
        required("SELECT COUNT(*) FROM quiz_sets WHERE id=? AND user_id=? AND status='READY'", id, userId, "Quiz not ready");
    }

    private void attempt(UUID id, UUID userId, String status) {
        String sql = "SELECT COUNT(*) FROM quiz_attempts WHERE id=? AND user_id=?" +
            (status == null ? "" : " AND status='" + status + "'");
        required(sql, id, userId, "Attempt not found or invalid state");
    }

    private Document owned(UUID id, UUID userId) {
        return documents.findById(id).filter(document -> document.getUserId().equals(userId))
            .orElseThrow(() -> new IllegalArgumentException("Document not found"));
    }

    private void required(String sql, UUID id, UUID userId, String message) {
        Integer count = jdbc.queryForObject(sql, Integer.class, id, userId);
        if (count == null || count == 0) throw new IllegalArgumentException(message);
    }

}
