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
public class FlashcardStudyService {
    private static final Set<String> GRADES = Set.of("AGAIN", "HARD", "GOOD", "EASY");
    private final LocalWorkspaceService users;
    private final DocumentRepository documents;
    private final JdbcTemplate jdbc;
    private final LearningMemoryService memory;

    public FlashcardStudyService(LocalWorkspaceService users, DocumentRepository documents, JdbcTemplate jdbc, LearningMemoryService memory) {
        this.users = users;
        this.documents = documents;
        this.jdbc = jdbc;
        this.memory = memory;
    }

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
            // Duplicate event with no prior scheduling state is a client-side
            // replay problem, surfaced as 400 instead of a 500.
            if(rows.isEmpty())throw new IllegalArgumentException("Duplicate review event has no scheduling state");
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

    private void deck(UUID id, UUID userId) {
        required("SELECT COUNT(*) FROM flashcard_decks WHERE id=? AND user_id=?", id, userId, "Deck not found");
    }

    private void card(UUID id, UUID userId) {
        required("SELECT COUNT(*) FROM flashcards f JOIN flashcard_decks d ON d.id=f.deck_id WHERE f.id=? AND d.user_id=?", id, userId, "Card not found");
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
