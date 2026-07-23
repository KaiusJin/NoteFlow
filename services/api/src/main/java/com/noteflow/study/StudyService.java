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
public class StudyService {
    private static final Set<String> GRADES=Set.of("AGAIN","HARD","GOOD","EASY");
    private final LocalWorkspaceService users; private final DocumentRepository documents; private final TaskDispatchService tasks; private final JdbcTemplate jdbc; private final LearningMemoryService memory;
    public StudyService(LocalWorkspaceService u,DocumentRepository d,TaskDispatchService t,JdbcTemplate j,LearningMemoryService m){users=u;documents=d;tasks=t;jdbc=j;memory=m;}

    public List<Map<String,Object>> decks(UUID id){UUID u=users.currentUserId();owned(id,u);return jdbc.queryForList("SELECT id,version,title,status,origin,source_scope_json,generation_options_json,total_source_groups,completed_source_groups,quality_report_json,error_message,created_at FROM flashcard_decks WHERE document_id=? AND user_id=? ORDER BY version DESC",id,u);}
    public List<Map<String,Object>> cards(UUID id){UUID u=users.currentUserId();deck(id,u);return jdbc.queryForList("SELECT id,card_type,front,back,cloze_text,difficulty,topic,hint,tags_json,source_pages_json,confidence FROM flashcards WHERE deck_id=? ORDER BY source_group_index,item_index",id);}
    public List<Map<String,Object>> dueCards(UUID id,int limit){UUID u=users.currentUserId();deck(id,u);return jdbc.queryForList("SELECT f.id,f.card_type,f.front,f.back,f.cloze_text,f.difficulty,f.topic,f.hint,f.source_pages_json,COALESCE(s.status,'NEW') review_status,s.due_at FROM flashcards f LEFT JOIN flashcard_review_states s ON s.flashcard_id=f.id AND s.user_id=? WHERE f.deck_id=? AND (s.flashcard_id IS NULL OR (s.status<>'SUSPENDED' AND s.due_at<=NOW())) ORDER BY s.due_at NULLS FIRST,f.item_index LIMIT ?",u,id,Math.max(1,Math.min(limit,500)));}

    @Transactional public Map<String,Object> review(UUID cardId,String raw){
        return review(cardId,raw,"flashcard-review:"+UUID.randomUUID());
    }

    @Transactional public Map<String,Object> review(UUID cardId,String raw,String externalEventId){
        UUID userId=users.currentUserId();
        card(cardId,userId);
        String grade=raw==null?"":raw.toUpperCase(Locale.ROOT);
        if(!GRADES.contains(grade))throw new IllegalArgumentException("Invalid review grade");
        if(externalEventId==null||externalEventId.isBlank())throw new IllegalArgumentException("eventId is required");
        String eventId=externalEventId.trim();
        if(eventId.length()>256)throw new IllegalArgumentException("eventId is too long");

        jdbc.queryForObject("SELECT pg_advisory_xact_lock(hashtext(?))",Object.class,"flashcard-review:"+userId+":"+cardId);
        var rows=jdbc.queryForList("SELECT status,ease_factor,interval_days,repetitions,due_at,last_grade FROM flashcard_review_states WHERE user_id=? AND flashcard_id=?",userId,cardId);
        String status="NEW"; double ease=2.5; int interval=0,repetitions=0;
        if(!rows.isEmpty()){
            var row=rows.getFirst(); status=(String)row.get("status");
            ease=((Number)row.get("ease_factor")).doubleValue();
            interval=((Number)row.get("interval_days")).intValue();
            repetitions=((Number)row.get("repetitions")).intValue();
        }
        if("SUSPENDED".equals(status))throw new IllegalArgumentException("Card is suspended");

        var card=jdbc.queryForMap("SELECT f.topic,f.difficulty,f.document_id FROM flashcards f WHERE f.id=?",cardId);
        Instant now=Instant.now();
        Map<String,Object> event=memory.record(new LearningEventRequest(eventId,"FLASHCARD_REVIEWED",now,
            List.of((String)card.get("topic")),(UUID)card.get("document_id"),"FLASHCARD",cardId,
            !"AGAIN".equals(grade),(String)card.get("difficulty"),null,false,grade,null,null,Map.of()));
        if(((Number)event.get("acceptedTopics")).intValue()==0){
            if(rows.isEmpty())throw new IllegalStateException("Duplicate review event has no scheduling state");
            var prior=rows.getFirst();
            Map<String,Object> result=new LinkedHashMap<>();
            result.put("status",prior.get("status")); result.put("intervalDays",prior.get("interval_days"));
            result.put("repetitions",prior.get("repetitions")); result.put("dueAt",prior.get("due_at"));
            result.put("grade",prior.get("last_grade")); result.put("eventId",eventId); result.put("duplicate",true);
            return result;
        }

        int quality=Map.of("AGAIN",1,"HARD",3,"GOOD",4,"EASY",5).get(grade);
        ease=Math.max(1.3,ease+(0.1-(5-quality)*(0.08+(5-quality)*0.02)));
        if("AGAIN".equals(grade)){repetitions=0;interval=1;status="LEARNING";}
        else{
            repetitions++;
            interval=repetitions==1?1:repetitions==2?6:Math.max(1,(int)Math.round(interval*ease));
            if("HARD".equals(grade))interval=Math.max(1,(int)Math.round(interval*.8));
            if("EASY".equals(grade))interval=Math.max(1,(int)Math.round(interval*1.3));
            status=repetitions>=2?"REVIEW":"LEARNING";
        }
        Instant due=now.plus(interval,ChronoUnit.DAYS);
        jdbc.update("INSERT INTO flashcard_review_states(user_id,flashcard_id,status,ease_factor,interval_days,repetitions,due_at,last_reviewed_at,last_grade) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,flashcard_id) DO UPDATE SET status=EXCLUDED.status,ease_factor=EXCLUDED.ease_factor,interval_days=EXCLUDED.interval_days,repetitions=EXCLUDED.repetitions,due_at=EXCLUDED.due_at,last_reviewed_at=EXCLUDED.last_reviewed_at,last_grade=EXCLUDED.last_grade,updated_at=NOW()",
            userId,cardId,status,ease,interval,repetitions,Timestamp.from(due),Timestamp.from(now),grade);
        Map<String,Object> result=new LinkedHashMap<>();
        result.put("status",status); result.put("intervalDays",interval); result.put("repetitions",repetitions);
        result.put("dueAt",due); result.put("grade",grade); result.put("eventId",eventId); result.put("duplicate",false);
        return result;
    }

    public List<Map<String,Object>> quizzes(UUID id){UUID u=users.currentUserId();owned(id,u);return jdbc.queryForList("SELECT id,version,title,status,origin,source_scope_json,generation_options_json,total_source_groups,completed_source_groups,quality_report_json,error_message,created_at FROM quiz_sets WHERE document_id=? AND user_id=? ORDER BY version DESC",id,u);}
    public List<Map<String,Object>> questions(UUID id){UUID u=users.currentUserId();quiz(id,u);return jdbc.queryForList("SELECT id,question_type,difficulty,topic,stem,options_json,points,source_pages_json FROM quiz_questions WHERE quiz_set_id=? ORDER BY source_group_index,item_index",id);}
    @Transactional public Map<String,Object> startAttempt(UUID setId){UUID u=users.currentUserId();quiz(setId,u);UUID id=UUID.randomUUID();jdbc.update("INSERT INTO quiz_attempts(id,quiz_set_id,user_id,status) VALUES (?,?,?,'IN_PROGRESS')",id,setId,u);return Map.of("attemptId",id,"status","IN_PROGRESS");}
    @Transactional public Map<String,Object> saveAnswer(UUID attemptId,UUID questionId,String response,Integer responseTimeMs,boolean hintUsed){UUID u=users.currentUserId();attempt(attemptId,u,"IN_PROGRESS");if(responseTimeMs!=null&&responseTimeMs<0)throw new IllegalArgumentException("responseTimeMs cannot be negative");int valid=jdbc.queryForObject("SELECT COUNT(*) FROM quiz_questions q JOIN quiz_attempts a ON a.quiz_set_id=q.quiz_set_id WHERE a.id=? AND q.id=?",Integer.class,attemptId,questionId);if(valid==0)throw new IllegalArgumentException("Question is not part of attempt");jdbc.update("INSERT INTO quiz_answers(id,attempt_id,question_id,user_response,response_time_ms,hint_used) VALUES (?,?,?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET user_response=EXCLUDED.user_response,response_time_ms=EXCLUDED.response_time_ms,hint_used=EXCLUDED.hint_used,is_correct=NULL,awarded_points=NULL,feedback=NULL,key_points_hit_json=NULL,graded_by=NULL,updated_at=NOW()",UUID.randomUUID(),attemptId,questionId,response==null?"":response,responseTimeMs,hintUsed);return Map.of("saved",true);}
    @Transactional public Map<String,Object> submit(UUID attemptId){UUID u=users.currentUserId();attempt(attemptId,u,"IN_PROGRESS");UUID documentId=jdbc.queryForObject("SELECT s.document_id FROM quiz_attempts a JOIN quiz_sets s ON s.id=a.quiz_set_id WHERE a.id=?",UUID.class,attemptId);jdbc.update("INSERT INTO quiz_answers(id,attempt_id,question_id,user_response) SELECT gen_random_uuid(),?,q.id,'' FROM quiz_questions q JOIN quiz_attempts a ON a.quiz_set_id=q.quiz_set_id WHERE a.id=? ON CONFLICT(attempt_id,question_id) DO NOTHING",attemptId,attemptId);for(var r:jdbc.queryForList("SELECT a.id,q.correct_answer,a.user_response,q.points,q.explanation FROM quiz_answers a JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=? AND q.question_type IN ('MULTIPLE_CHOICE','TRUE_FALSE')",attemptId)){boolean ok=norm((String)r.get("correct_answer")).equals(norm((String)r.get("user_response")));jdbc.update("UPDATE quiz_answers SET is_correct=?,awarded_points=?,feedback=?,key_points_hit_json='[]',graded_by='AUTO',updated_at=NOW() WHERE id=?",ok,ok?((Number)r.get("points")).doubleValue():0d,r.get("explanation"),r.get("id"));}int free=jdbc.queryForObject("SELECT COUNT(*) FROM quiz_answers a JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=? AND q.question_type NOT IN ('MULTIPLE_CHOICE','TRUE_FALSE')",Integer.class,attemptId);if(free>0){jdbc.update("UPDATE quiz_attempts SET status='GRADING',submitted_at=NOW(),updated_at=NOW() WHERE id=?",attemptId);Task t=tasks.createAndEnqueue(documentId,u,TaskType.GRADE_QUIZ_ATTEMPT,attemptId);return Map.of("attemptId",attemptId,"taskId",t.getId(),"status","GRADING");}jdbc.update("UPDATE quiz_attempts a SET status='COMPLETED',score=x.score,max_score=x.maximum,submitted_at=NOW(),completed_at=NOW(),updated_at=NOW() FROM (SELECT COALESCE(SUM(ans.awarded_points),0) score,COALESCE(SUM(q.points),0) maximum FROM quiz_answers ans JOIN quiz_questions q ON q.id=ans.question_id WHERE ans.attempt_id=?) x WHERE a.id=?",attemptId,attemptId);memory.recordQuizAttempt(attemptId);return Map.of("attemptId",attemptId,"status","COMPLETED");}
    public Map<String,Object> attemptResult(UUID id){UUID u=users.currentUserId();attempt(id,u,null);Map<String,Object> meta=jdbc.queryForMap("SELECT a.id,a.quiz_set_id,a.status,a.score,a.max_score,a.weak_topics_json,a.started_at,a.submitted_at,a.completed_at,s.generation_options_json FROM quiz_attempts a JOIN quiz_sets s ON s.id=a.quiz_set_id WHERE a.id=?",id);var answers=jdbc.queryForList("SELECT ans.question_id,ans.user_response,ans.is_correct,ans.awarded_points,ans.feedback,ans.graded_by,q.stem,q.question_type,q.difficulty,q.topic,q.options_json,q.correct_answer,q.explanation,q.points FROM quiz_answers ans JOIN quiz_questions q ON q.id=ans.question_id WHERE ans.attempt_id=? ORDER BY q.source_group_index,q.item_index",id);return Map.of("attempt",meta,"answers",answers);}

    private String norm(String v){return v==null?"":v.trim().toUpperCase(Locale.ROOT);}private Document ownedReady(UUID id,UUID u){Document d=owned(id,u);if(d.getStatus()!=DocumentStatus.READY)throw new IllegalArgumentException("Document must be READY");return d;}private Document owned(UUID id,UUID u){return documents.findById(id).filter(d->d.getUserId().equals(u)).orElseThrow(()->new IllegalArgumentException("Document not found"));}private void deck(UUID id,UUID u){required("SELECT COUNT(*) FROM flashcard_decks WHERE id=? AND user_id=?",id,u,"Deck not found");}private void card(UUID id,UUID u){required("SELECT COUNT(*) FROM flashcards f JOIN flashcard_decks d ON d.id=f.deck_id WHERE f.id=? AND d.user_id=?",id,u,"Card not found");}private void quiz(UUID id,UUID u){required("SELECT COUNT(*) FROM quiz_sets WHERE id=? AND user_id=? AND status='READY'",id,u,"Quiz not ready");}private void attempt(UUID id,UUID u,String status){String sql="SELECT COUNT(*) FROM quiz_attempts WHERE id=? AND user_id=?"+(status==null?"":" AND status='"+status+"'");required(sql,id,u,"Attempt not found or invalid state");}private void required(String sql,UUID id,UUID u,String msg){Integer n=jdbc.queryForObject(sql,Integer.class,id,u);if(n==null||n==0)throw new IllegalArgumentException(msg);}
}
