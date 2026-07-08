package com.noteflow.study;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.web.bind.annotation.*;

@RestController
public class StudyController {
    private final StudyService study;
    public StudyController(StudyService study) { this.study = study; }

    @PostMapping("/documents/{id}/flashcard-decks") public Map<String,Object> generateDeck(@PathVariable UUID id){return study.generateDeck(id);}
    @GetMapping("/documents/{id}/flashcard-decks") public List<Map<String,Object>> decks(@PathVariable UUID id){return study.decks(id);}
    @GetMapping("/flashcard-decks/{id}/cards") public List<Map<String,Object>> cards(@PathVariable UUID id){return study.cards(id);}
    @GetMapping("/flashcard-decks/{id}/reviews/due") public List<Map<String,Object>> due(@PathVariable UUID id,@RequestParam(defaultValue="100") int limit){return study.dueCards(id,limit);}
    @PostMapping("/flashcards/{id}/reviews") public Map<String,Object> review(@PathVariable UUID id,@RequestBody ReviewRequest r){return study.review(id,r.grade());}

    @PostMapping("/documents/{id}/quiz-sets") public Map<String,Object> generateQuiz(@PathVariable UUID id,@RequestBody(required=false) QuizOptionsRequest r){return study.generateQuiz(id,r==null?null:r.easy(),r==null?null:r.medium(),r==null?null:r.hard());}
    @GetMapping("/documents/{id}/quiz-sets") public List<Map<String,Object>> quizzes(@PathVariable UUID id){return study.quizzes(id);}
    @GetMapping("/quiz-sets/{id}/questions") public List<Map<String,Object>> questions(@PathVariable UUID id){return study.questions(id);}
    @PostMapping("/quiz-sets/{id}/attempts") public Map<String,Object> start(@PathVariable UUID id){return study.startAttempt(id);}
    @PutMapping("/quiz-attempts/{attemptId}/answers/{questionId}") public Map<String,Object> answer(@PathVariable UUID attemptId,@PathVariable UUID questionId,@RequestBody AnswerRequest r){return study.saveAnswer(attemptId,questionId,r.response());}
    @PostMapping("/quiz-attempts/{id}/submit") public Map<String,Object> submit(@PathVariable UUID id){return study.submit(id);}
    @GetMapping("/quiz-attempts/{id}") public Map<String,Object> attempt(@PathVariable UUID id){return study.attemptResult(id);}

    public record ReviewRequest(String grade) {}
    public record AnswerRequest(String response) {}
    public record QuizOptionsRequest(Integer easy, Integer medium, Integer hard) {}
}
