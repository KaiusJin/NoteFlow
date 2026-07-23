package com.noteflow.learningmemory;

import java.time.Instant;
import java.util.*;
import org.springframework.web.bind.annotation.*;

@RestController @RequestMapping("/learning-memory")
public class AdvancedLearningMemoryController {
 private final AdvancedLearningMemoryService service; private final LearningMemoryService memory;
 public AdvancedLearningMemoryController(AdvancedLearningMemoryService s,LearningMemoryService m){service=s;memory=m;}
 @GetMapping("/goals") public List<Map<String,Object>> goals(@RequestParam(defaultValue="false") boolean all){return service.goals(all);}
 @PutMapping("/goals") public Map<String,Object> goal(@RequestBody Goal r){return service.saveGoal(r.id,r.title,r.description,r.deadline,r.priority,r.topics,r.documentIds);}
 @PatchMapping("/goals/{id}/status") public void goalStatus(@PathVariable UUID id,@RequestBody Status r){service.goalStatus(id,r.status);}
 @GetMapping("/preferences") public List<Map<String,Object>> preferences(){return service.preferences();}
 @PutMapping("/preferences/{key}") public Map<String,Object> preference(@PathVariable String key,@RequestBody Preference r){return service.setPreference(key,r.value,r.source,r.confidence==null?1:r.confidence);}
 @PostMapping("/artifacts") public Map<String,Object> artifact(@RequestBody Artifact r){return service.linkArtifact(r.topic,r.type,r.artifactId,r.title,r.documentId,r.metadata);}
 @GetMapping("/artifacts") public List<Map<String,Object>> artifacts(@RequestParam String topic,@RequestParam(defaultValue="20") int limit){return service.artifacts(topic,limit);}
 @PostMapping("/topic-edges") public void edge(@RequestBody Edge r){service.linkTopics(r.from,r.to,r.relation,r.weight==null?.5:r.weight,r.source);}
 @GetMapping("/topic-graph") public List<Map<String,Object>> graph(@RequestParam String topic,@RequestParam(defaultValue="2") int depth){return service.topicGraph(topic,depth);}
 @PostMapping("/corrections") public Map<String,Object> correct(@RequestBody Correction r){return service.correct(r.topic,r.scopeId,r.mastery,r.active,r.reason,r.expectedVersion);}
 @GetMapping("/topics/{topic}/trend") public List<Map<String,Object>> trend(@PathVariable String topic,@RequestParam(defaultValue="50") int limit){return service.trend(topic,limit);}
 @PostMapping("/experiments/{key}/assign") public Map<String,Object> assign(@PathVariable String key,@RequestBody Variants r){return service.assignExperiment(key,r.variants);}
 @PostMapping("/experiments/{key}/outcomes") public void outcome(@PathVariable String key,@RequestBody Outcome r){service.experimentOutcome(key,r.outcome);}
 @PostMapping("/study-plans") public Map<String,Object> plan(@RequestBody(required=false) Plan r){return service.buildPlan(r==null?null:r.title,r==null||r.minutes==null?60:r.minutes);}
 @PostMapping("/expiration/run") public Map<String,Object> expire(@RequestParam(defaultValue="365") int days){return Map.of("expired",service.expireStale(days));}
 @PostMapping("/topics/{topic}/recalculate") public Map<String,Object> recalculate(@PathVariable String topic,@RequestParam(required=false) UUID scopeId){return memory.recalculate(topic,scopeId);}
 public record Goal(UUID id,String title,String description,Instant deadline,Integer priority,List<String> topics,List<UUID> documentIds){}
 public record Status(String status){} public record Preference(Object value,String source,Double confidence){}
 public record Artifact(String topic,String type,UUID artifactId,String title,UUID documentId,Map<String,Object> metadata){}
 public record Edge(String from,String to,String relation,Double weight,String source){}
 public record Correction(String topic,UUID scopeId,Double mastery,Boolean active,String reason,Long expectedVersion){}
 public record Variants(List<String> variants){} public record Outcome(double outcome){} public record Plan(String title,Integer minutes){}
}
