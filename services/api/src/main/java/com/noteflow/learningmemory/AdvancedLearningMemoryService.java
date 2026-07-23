package com.noteflow.learningmemory;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.noteflow.workspace.LocalWorkspaceService;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.sql.Timestamp;
import java.text.Normalizer;
import java.time.Instant;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AdvancedLearningMemoryService {
    private final JdbcTemplate jdbc; private final LocalWorkspaceService workspaces;
    private final LearningMemoryService memory; private final ObjectMapper json;
    public AdvancedLearningMemoryService(JdbcTemplate j,LocalWorkspaceService w,LearningMemoryService m,ObjectMapper o){jdbc=j;workspaces=w;memory=m;json=o;}

    public List<Map<String,Object>> goals(boolean includeCompleted){return jdbc.queryForList("SELECT * FROM learning_goals WHERE workspace_id=? "+(includeCompleted?"":"AND status='ACTIVE' ")+"ORDER BY priority DESC,deadline NULLS LAST,updated_at DESC",workspaces.currentWorkspaceId());}
    @Transactional public Map<String,Object> saveGoal(UUID id,String title,String description,Instant deadline,Integer priority,List<String> topics,List<UUID> documents){UUID goal=id==null?UUID.randomUUID():id;int p=Math.max(0,Math.min(100,priority==null?50:priority));validateDocuments(documents);jdbc.update("""
      INSERT INTO learning_goals(id,workspace_id,title,description,deadline,priority,topic_keys_json,document_ids_json)
      VALUES (?,?,?,?,?,?,?::jsonb,?::jsonb) ON CONFLICT(id) DO UPDATE SET title=EXCLUDED.title,description=EXCLUDED.description,
      deadline=EXCLUDED.deadline,priority=EXCLUDED.priority,topic_keys_json=EXCLUDED.topic_keys_json,
      document_ids_json=EXCLUDED.document_ids_json,version=learning_goals.version+1,updated_at=NOW()
      WHERE learning_goals.workspace_id=EXCLUDED.workspace_id""",goal,workspaces.currentWorkspaceId(),required(title),description==null?"":description,
      deadline==null?null:Timestamp.from(deadline),p,toJson(normalizeTopics(topics)),toJson(documents==null?List.of():documents));return jdbc.queryForMap("SELECT * FROM learning_goals WHERE id=? AND workspace_id=?",goal,workspaces.currentWorkspaceId());}
    @Transactional public void goalStatus(UUID id,String status){String s=status.toUpperCase(Locale.ROOT);if(!Set.of("ACTIVE","COMPLETED","CANCELLED").contains(s))throw new IllegalArgumentException("Invalid goal status");if(jdbc.update("UPDATE learning_goals SET status=?,version=version+1,updated_at=NOW() WHERE id=? AND workspace_id=?",s,id,workspaces.currentWorkspaceId())!=1)throw new IllegalArgumentException("Goal not found");}

    public List<Map<String,Object>> preferences(){return jdbc.queryForList("SELECT preference_key,value_json,source,confidence,evidence_count,version,updated_at FROM learning_preferences WHERE workspace_id=? AND (source='EXPLICIT' OR evidence_count>=5) ORDER BY preference_key",workspaces.currentWorkspaceId());}
    @Transactional public Map<String,Object> setPreference(String key,Object value,String source,double confidence){String k=required(key).toLowerCase(Locale.ROOT);String s=source==null?"EXPLICIT":source.toUpperCase(Locale.ROOT);if(!Set.of("EXPLICIT","BEHAVIOR").contains(s))throw new IllegalArgumentException("Invalid preference source");double c="EXPLICIT".equals(s)?1:Math.max(0,Math.min(.95,confidence));jdbc.update("""
      INSERT INTO learning_preferences(workspace_id,preference_key,value_json,source,confidence)
      VALUES (?,?,?::jsonb,?,?) ON CONFLICT(workspace_id,preference_key) DO UPDATE SET
      value_json=CASE WHEN EXCLUDED.source='EXPLICIT' OR learning_preferences.source<>'EXPLICIT' THEN EXCLUDED.value_json ELSE learning_preferences.value_json END,
      source=CASE WHEN EXCLUDED.source='EXPLICIT' THEN 'EXPLICIT' ELSE learning_preferences.source END,
      confidence=CASE WHEN EXCLUDED.source='EXPLICIT' THEN 1 ELSE LEAST(.95,GREATEST(learning_preferences.confidence,EXCLUDED.confidence)) END,
      evidence_count=learning_preferences.evidence_count+1,version=learning_preferences.version+1,updated_at=NOW()""",
      workspaces.currentWorkspaceId(),k,toJson(value),s,c);return jdbc.queryForMap("SELECT * FROM learning_preferences WHERE workspace_id=? AND preference_key=?",workspaces.currentWorkspaceId(),k);}

    @Transactional public Map<String,Object> linkArtifact(String topic,String type,UUID artifactId,String title,UUID documentId,Map<String,Object> metadata){String key=topicKey(topic),t=required(type).toUpperCase(Locale.ROOT);validateDocuments(documentId==null?List.of():List.of(documentId));validateArtifact(t,artifactId);jdbc.update("""
      INSERT INTO learning_artifact_links(workspace_id,topic_key,artifact_type,artifact_id,title,document_id,metadata_json)
      VALUES (?,?,?,?,?,?,?::jsonb) ON CONFLICT(workspace_id,topic_key,artifact_type,artifact_id) DO UPDATE SET
      title=EXCLUDED.title,document_id=EXCLUDED.document_id,metadata_json=EXCLUDED.metadata_json,status='ACTIVE',updated_at=NOW()""",
      workspaces.currentWorkspaceId(),key,t,artifactId,title==null?"":title,documentId,toJson(metadata==null?Map.of():metadata));return jdbc.queryForMap("SELECT * FROM learning_artifact_links WHERE workspace_id=? AND topic_key=? AND artifact_type=? AND artifact_id=?",workspaces.currentWorkspaceId(),key,t,artifactId);}
    public List<Map<String,Object>> artifacts(String topic,int limit){return jdbc.queryForList("SELECT * FROM learning_artifact_links WHERE workspace_id=? AND topic_key=? AND status='ACTIVE' ORDER BY last_interacted_at DESC NULLS LAST,updated_at DESC LIMIT ?",workspaces.currentWorkspaceId(),topicKey(topic),Math.max(1,Math.min(100,limit)));}

    @Transactional public void linkTopics(String from,String to,String relation,double weight,String source){jdbc.update("""
      INSERT INTO learning_topic_edges(workspace_id,from_topic_key,to_topic_key,relation,weight,source)
      VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id,from_topic_key,to_topic_key,relation) DO UPDATE SET
      weight=(learning_topic_edges.weight*learning_topic_edges.evidence_count+EXCLUDED.weight)/(learning_topic_edges.evidence_count+1),
      evidence_count=learning_topic_edges.evidence_count+1,updated_at=NOW()""",workspaces.currentWorkspaceId(),topicKey(from),topicKey(to),required(relation).toUpperCase(Locale.ROOT),Math.max(0,Math.min(1,weight)),source==null?"MANUAL":source);}
    public List<Map<String,Object>> topicGraph(String topic,int depth){String key=topicKey(topic);return jdbc.queryForList("""
      WITH RECURSIVE graph(from_topic_key,to_topic_key,relation,weight,depth,path) AS (
      SELECT from_topic_key,to_topic_key,relation,weight,1,ARRAY[from_topic_key::text,to_topic_key::text] FROM learning_topic_edges WHERE workspace_id=? AND from_topic_key=?
      UNION ALL SELECT e.from_topic_key,e.to_topic_key,e.relation,e.weight,g.depth+1,g.path||e.to_topic_key::text FROM learning_topic_edges e JOIN graph g ON e.from_topic_key=g.to_topic_key
      WHERE e.workspace_id=? AND g.depth<? AND NOT e.to_topic_key=ANY(g.path)) SELECT from_topic_key,to_topic_key,relation,weight,depth FROM graph ORDER BY depth,weight DESC LIMIT 200""",workspaces.currentWorkspaceId(),key,workspaces.currentWorkspaceId(),Math.max(1,Math.min(4,depth)));}

    @Transactional public Map<String,Object> correct(String topic,UUID scopeId,Double mastery,Boolean active,String reason,Long expectedVersion){
        if(expectedVersion==null)throw new IllegalArgumentException("expectedVersion is required");
        UUID workspaceId=workspaces.currentWorkspaceId(),scope=scopeId==null?workspaceId:scopeId;
        String key=topicKey(topic);
        lockTopic(workspaceId,scope,key);
        Map<String,Object> old=jdbc.queryForMap("SELECT mastery,is_active,version FROM topic_learning_memory WHERE workspace_id=? AND scope_id=? AND topic_key=?",workspaceId,scope,key);
        double value=mastery==null?((Number)old.get("mastery")).doubleValue():Math.max(0,Math.min(1,mastery));
        boolean enabled=active==null?(Boolean)old.get("is_active"):active;
        int changed=jdbc.update("UPDATE topic_learning_memory SET mastery=?,is_active=?,confidence=CASE WHEN ? IS NULL THEN confidence ELSE 1 END,version=version+1,updated_at=NOW() WHERE workspace_id=? AND scope_id=? AND topic_key=? AND version=?",
            value,enabled,mastery,workspaceId,scope,key,expectedVersion);
        if(changed!=1)throw new IllegalStateException("Learning memory changed; reload before correcting");
        Map<String,Object> now=Map.of("mastery",value,"active",enabled,"version",expectedVersion+1);
        jdbc.update("INSERT INTO learning_memory_corrections(id,workspace_id,scope_id,topic_key,correction_type,old_value_json,new_value_json,reason) VALUES (?,?,?,?,?,?::jsonb,?::jsonb,?)",
            UUID.randomUUID(),workspaceId,scope,key,enabled?"CORRECT":"EXPIRE",toJson(old),toJson(now),required(reason));
        return now;
    }

    public List<Map<String,Object>> trend(String topic,int limit){return jdbc.queryForList("SELECT mastery,confidence,attempts,recent_trend,algorithm_version,recorded_at FROM learning_memory_history WHERE workspace_id=? AND topic_key=? ORDER BY recorded_at DESC LIMIT ?",workspaces.currentWorkspaceId(),topicKey(topic),Math.max(2,Math.min(500,limit)));}

    @Transactional public Map<String,Object> assignExperiment(String key,List<String> variants){if(variants==null||variants.size()<2)throw new IllegalArgumentException("At least two variants required");String hash=sha256(workspaces.currentWorkspaceId()+":"+required(key));int index=Math.floorMod(hash.hashCode(),variants.size());jdbc.update("INSERT INTO learning_strategy_experiments(workspace_id,experiment_key,variant,assignment_hash) VALUES (?,?,?,?) ON CONFLICT(workspace_id,experiment_key) DO NOTHING",workspaces.currentWorkspaceId(),key,variants.get(index),hash);return jdbc.queryForMap("SELECT * FROM learning_strategy_experiments WHERE workspace_id=? AND experiment_key=?",workspaces.currentWorkspaceId(),key);}
    @Transactional public void experimentOutcome(String key,double outcome){jdbc.update("UPDATE learning_strategy_experiments SET outcome_sum=outcome_sum+?,outcome_count=outcome_count+1,updated_at=NOW() WHERE workspace_id=? AND experiment_key=?",Math.max(-1,Math.min(1,outcome)),workspaces.currentWorkspaceId(),key);}

    @Transactional public int expireStale(int days){Integer count=jdbc.queryForObject("""
      WITH expired AS (UPDATE topic_learning_memory SET is_active=FALSE,version=version+1,updated_at=NOW()
        WHERE workspace_id=? AND is_active AND confidence<.25 AND last_activity_at<NOW()-make_interval(days=>?)
        RETURNING workspace_id,scope_id,topic_key,mastery),logged AS (
        INSERT INTO learning_memory_corrections(id,workspace_id,scope_id,topic_key,correction_type,old_value_json,new_value_json,reason)
        SELECT gen_random_uuid(),workspace_id,scope_id,topic_key,'EXPIRE',jsonb_build_object('mastery',mastery,'active',true),
          jsonb_build_object('mastery',mastery,'active',false),'Automatic low-confidence expiration' FROM expired RETURNING 1)
      SELECT COUNT(*) FROM logged""",Integer.class,workspaces.currentWorkspaceId(),Math.max(30,days));return count==null?0:count;}

    @Scheduled(cron="${noteflow.learning-memory.expiration-cron:0 17 3 * * *}")
    @Transactional public void scheduledExpiration(){expireStale(365);}

    @Transactional public Map<String,Object> buildPlan(String title,int minutes){
        UUID workspaceId=workspaces.currentWorkspaceId();
        List<Map<String,Object>> activeGoals=goals(false),prefs=preferences();
        Map<String,Object> goal=activeGoals.isEmpty()?null:activeGoals.getFirst();
        List<UUID> documentScope=goal==null?List.of():jsonUuidList(goal.get("document_ids_json"));
        List<Map<String,Object>> weak=memory.weakTopics(documentScope,12),due=memory.dueReviews(documentScope,12);
        Set<String> goalTopics=goal==null?Set.of():jsonStringSet(goal.get("topic_keys_json"));
        Comparator<Map<String,Object>> goalFirst=Comparator.comparing((Map<String,Object> row)->!goalTopics.contains(String.valueOf(row.get("topic_key"))));
        due.sort(goalFirst); weak.sort(goalFirst);

        Map<String,Object> preferenceValues=preferenceValues(prefs);
        String preferredFormat=String.valueOf(preferenceValues.getOrDefault("practice_format","QUIZ")).toUpperCase(Locale.ROOT);
        String weakAction="FLASHCARDS".equals(preferredFormat)?"TARGETED_FLASHCARDS":"TARGETED_QUIZ";
        int reviewBlock=intPreference(preferenceValues,"review_block_minutes",15,5,60);
        int practiceBlock=intPreference(preferenceValues,"practice_block_minutes",20,5,90);
        boolean urgent=goal!=null&&goal.get("deadline") instanceof Timestamp deadline&&deadline.toInstant().isBefore(Instant.now().plusSeconds(14L*86400));
        if(urgent){reviewBlock=Math.min(60,reviewBlock+5);practiceBlock=Math.min(90,practiceBlock+5);}

        Map<String,Object> experiment=assignExperiment("dynamic-plan-order",List.of("DUE_FIRST","WEAK_FIRST"));
        boolean dueFirst="DUE_FIRST".equals(experiment.get("variant"));
        int budget=Math.max(10,Math.min(480,minutes)),remaining=budget;
        List<Map<String,Object>> tasks=new ArrayList<>(); Set<String> used=new HashSet<>();
        if(dueFirst){remaining=addTasks(due,tasks,used,"REVIEW",reviewBlock,remaining,preferenceValues);addTasks(weak,tasks,used,weakAction,practiceBlock,remaining,preferenceValues);}
        else{remaining=addTasks(weak,tasks,used,weakAction,practiceBlock,remaining,preferenceValues);addTasks(due,tasks,used,"REVIEW",reviewBlock,remaining,preferenceValues);}
        if(tasks.isEmpty())tasks.add(Map.of("action","EXPLORE","topic","New material","minutes",budget,"reason","No due or weak topic in the active goal scope"));

        int total=tasks.stream().mapToInt(task->((Number)task.get("minutes")).intValue()).sum();
        UUID id=UUID.randomUUID(),goalId=goal==null?null:(UUID)goal.get("id");
        Map<String,Object> plan=new LinkedHashMap<>(); plan.put("goal",goal); plan.put("preferences",prefs);
        plan.put("experiment",Map.of("key","dynamic-plan-order","variant",experiment.get("variant")));
        plan.put("urgentDeadline",urgent); plan.put("documentScope",documentScope); plan.put("tasks",tasks);
        plan.put("generatedAt",Instant.now().toString());
        jdbc.update("INSERT INTO learning_study_plans(id,workspace_id,title,goal_id,plan_json,estimated_minutes) VALUES (?,?,?,?,?::jsonb,?)",
            id,workspaceId,title==null?"Today's Study Plan":title,goalId,toJson(plan),total);
        return jdbc.queryForMap("SELECT * FROM learning_study_plans WHERE id=? AND workspace_id=?",id,workspaceId);
    }

    private int addTasks(List<Map<String,Object>> rows,List<Map<String,Object>> tasks,Set<String> used,String action,int block,
                         int remaining,Map<String,Object> preferences){
        for(Map<String,Object> row:rows){
            if(remaining<=0)break;
            if(used.add(String.valueOf(row.get("topic_key")))){
                int allocation=Math.min(block,remaining); tasks.add(task(row,action,allocation,preferences)); remaining-=allocation;
            }
        }
        return remaining;
    }

    private Map<String,Object> task(Map<String,Object> row,String requestedAction,int minutes,Map<String,Object> preferences){
        List<Map<String,Object>> existing=artifacts(String.valueOf(row.get("topic")),1);
        String action=existing.isEmpty()?requestedAction:"REUSE_"+existing.getFirst().get("artifact_type");
        Map<String,Object> result=new LinkedHashMap<>(); result.put("action",action); result.put("topic",row.get("topic"));
        result.put("minutes",minutes); result.put("reason",row.getOrDefault("reason",requestedAction.equals("REVIEW")?"Review is due":"Low mastery"));
        String topicKey=String.valueOf(row.get("topic_key"));
        Object observedFormat=preferences.get("practice_format_topic:"+sha256(topicKey).substring(0,64));
        if(observedFormat!=null&&requestedAction.startsWith("TARGETED_"))result.put("action","FLASHCARDS".equals(String.valueOf(observedFormat).toUpperCase(Locale.ROOT))?"TARGETED_FLASHCARDS":"TARGETED_QUIZ");
        Object difficulty=preferences.get("topic_difficulty:"+sha256(topicKey).substring(0,64));
        if(difficulty!=null)result.put("difficultyAdjustment",difficulty);
        if(!existing.isEmpty())result.put("artifact",existing.getFirst());
        return result;
    }

    private String toJson(Object o){try{return json.writeValueAsString(o);}catch(JsonProcessingException e){throw new IllegalArgumentException("Invalid JSON",e);}}
    private static String required(String s){if(s==null||s.isBlank())throw new IllegalArgumentException("Required value missing");return s.trim();}
    private static String topicKey(String s){return Normalizer.normalize(required(s),Normalizer.Form.NFKC).toLowerCase(Locale.ROOT).replaceAll("\\s+"," ");}
    private static List<String> normalizeTopics(List<String> values){return values==null?List.of():values.stream().map(AdvancedLearningMemoryService::topicKey).distinct().limit(100).toList();}
    private Set<String> jsonStringSet(Object value){try{List<?> parsed=json.readValue(String.valueOf(value),List.class);Set<String> result=new HashSet<>();for(Object item:parsed)result.add(String.valueOf(item));return result;}catch(Exception ignored){return Set.of();}}
    private List<UUID> jsonUuidList(Object value){try{List<?> parsed=json.readValue(String.valueOf(value),List.class);List<UUID> result=new ArrayList<>();for(Object item:parsed)result.add(UUID.fromString(String.valueOf(item)));return result;}catch(Exception ignored){return List.of();}}
    private Map<String,Object> preferenceValues(List<Map<String,Object>> rows){Map<String,Object> result=new HashMap<>();for(Map<String,Object> row:rows){try{result.put(String.valueOf(row.get("preference_key")),json.readValue(String.valueOf(row.get("value_json")),Object.class));}catch(Exception ignored){}}return result;}
    private static int intPreference(Map<String,Object> values,String key,int fallback,int minimum,int maximum){Object value=values.get(key);if(!(value instanceof Number number))return fallback;return Math.max(minimum,Math.min(maximum,number.intValue()));}
    private static String sha256(String s){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));}catch(Exception e){throw new IllegalStateException(e);}}
    private void lockTopic(UUID workspaceId,UUID scopeId,String key){jdbc.queryForObject("SELECT pg_advisory_xact_lock(hashtext(?))",Object.class,"learning-memory:"+workspaceId+":"+scopeId+":"+key);}
    private void validateDocuments(List<UUID> ids){if(ids==null)return;UUID workspaceId=workspaces.currentWorkspaceId();for(UUID id:ids.stream().filter(Objects::nonNull).distinct().limit(100).toList()){Integer count=jdbc.queryForObject("SELECT COUNT(*) FROM documents WHERE id=? AND user_id=?",Integer.class,id,workspaceId);if(count==null||count!=1)throw new IllegalArgumentException("Document not found");}}
    private void validateArtifact(String type,UUID artifactId){if(artifactId==null)throw new IllegalArgumentException("artifactId is required");String table=type.equals("QUIZ")?"quiz_sets":type.equals("FLASHCARDS")?"flashcard_decks":"notes";Integer count=jdbc.queryForObject("SELECT COUNT(*) FROM "+table+" WHERE id=? AND user_id=?",Integer.class,artifactId,workspaces.currentWorkspaceId());if(count==null||count!=1)throw new IllegalArgumentException("Artifact not found");}
}
