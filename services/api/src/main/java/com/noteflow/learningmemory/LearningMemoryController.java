package com.noteflow.learningmemory;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/learning-memory")
public class LearningMemoryController {
    private final LearningMemoryService memory;

    public LearningMemoryController(LearningMemoryService memory) { this.memory = memory; }

    @PostMapping("/events")
    public Map<String,Object> collect(@RequestBody LearningEventRequest request) { return memory.record(request); }

    @PostMapping("/feedback")
    public Map<String,Object> feedback(@RequestBody LearningFeedbackRequest request) { return memory.recordFeedback(request); }

    @GetMapping("/profile")
    public Map<String,Object> profile(@RequestParam(required=false) List<UUID> documentIds,
                                      @RequestParam(defaultValue="50") int limit) {
        return memory.profile(documentIds, limit);
    }

    @GetMapping("/weak-topics")
    public List<Map<String,Object>> weakTopics(@RequestParam(required=false) List<UUID> documentIds,
                                               @RequestParam(defaultValue="20") int limit) {
        return memory.weakTopics(documentIds, limit);
    }

    @GetMapping("/due-reviews")
    public List<Map<String,Object>> dueReviews(@RequestParam(required=false) List<UUID> documentIds,
                                               @RequestParam(defaultValue="20") int limit) {
        return memory.dueReviews(documentIds, limit);
    }

    @GetMapping("/topics/{topic}/explanation")
    public Map<String,Object> explanation(@PathVariable String topic,
                                          @RequestParam(required=false) List<UUID> documentIds) {
        return memory.explain(topic, documentIds);
    }
}
