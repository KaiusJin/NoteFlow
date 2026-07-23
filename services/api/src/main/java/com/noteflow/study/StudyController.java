package com.noteflow.study;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class StudyController {
    private final StudyService study;
    private final QuizGenerationService quizzes;
    private final FlashcardGenerationService flashcards;
    public StudyController(StudyService study, QuizGenerationService quizzes, FlashcardGenerationService flashcards) {
        this.study = study;
        this.quizzes = quizzes;
        this.flashcards = flashcards;
    }

    @PostMapping("/documents/{id}/flashcard-decks") public Map<String,Object> generateDeck(@PathVariable UUID id,@RequestBody(required=false) FlashcardOptionsRequest r){
        return flashcards.generate(r == null
            ? FlashcardGenerationRequest.section(id)
            : new FlashcardGenerationRequest(List.of(id),List.of(),r.section(),r.focus(),r.title(),r.count(),r.groupBySection(),"SECTION"));
    }
    @GetMapping("/documents/{id}/flashcard-decks") public List<Map<String,Object>> decks(@PathVariable UUID id){return study.decks(id);}
    @GetMapping("/flashcard-decks/{id}/cards") public List<Map<String,Object>> cards(@PathVariable UUID id){return study.cards(id);}
    @GetMapping("/flashcard-decks/{id}/reviews/due") public List<Map<String,Object>> due(@PathVariable UUID id,@RequestParam(defaultValue="100") int limit){return study.dueCards(id,limit);}
    @PostMapping("/flashcards/{id}/reviews") public Map<String,Object> review(@PathVariable UUID id,@RequestBody ReviewRequest r){
        if (r.eventId()==null || r.eventId().isBlank())
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"eventId is required for idempotent review submission");
        return study.review(id,r.grade(),r.eventId());
    }

    @PostMapping("/documents/{id}/quiz-sets") public Map<String,Object> generateQuiz(@PathVariable UUID id,@RequestBody(required=false) QuizOptionsRequest r){
        return quizzes.generate(r == null
            ? QuizGenerationRequest.section(id,null,null,null)
            : new QuizGenerationRequest(List.of(id),List.of(),r.section(),r.focus(),r.title(),r.easy(),r.medium(),r.hard(),r.questionTypes(),r.includeExplanations(),"SECTION"));
    }

    /** Typed adapters used by the local conversation Agent. */
    @PostMapping("/internal/study/quiz-generations") public Map<String,Object> createTargetedQuiz(@RequestBody QuizGenerationRequest request){return quizzes.generate(request);}
    @PostMapping("/internal/study/flashcard-generations") public Map<String,Object> createContextFlashcards(@RequestBody FlashcardGenerationRequest request){return flashcards.generate(request);}
    @GetMapping("/documents/{id}/quiz-sets") public List<Map<String,Object>> quizzes(@PathVariable UUID id){return study.quizzes(id);}
    @GetMapping("/quiz-sets/{id}/questions") public List<Map<String,Object>> questions(@PathVariable UUID id){return study.questions(id);}
    @PostMapping("/quiz-sets/{id}/attempts") public Map<String,Object> start(@PathVariable UUID id){return study.startAttempt(id);}
    @PutMapping("/quiz-attempts/{attemptId}/answers/{questionId}") public Map<String,Object> answer(@PathVariable UUID attemptId,@PathVariable UUID questionId,@RequestBody AnswerRequest r){return study.saveAnswer(attemptId,questionId,r.response(),r.responseTimeMs(),Boolean.TRUE.equals(r.hintUsed()));}
    @PostMapping("/quiz-attempts/{id}/submit") public Map<String,Object> submit(@PathVariable UUID id){return study.submit(id);}
    @GetMapping("/quiz-attempts/{id}") public Map<String,Object> attempt(@PathVariable UUID id){return study.attemptResult(id);}

    public record ReviewRequest(String grade, String eventId) {}
    public record AnswerRequest(String response,Integer responseTimeMs,Boolean hintUsed) {}
    public record QuizOptionsRequest(Integer easy, Integer medium, Integer hard, List<String> questionTypes,
                                     Boolean includeExplanations, String section, String focus, String title) {}
    public record FlashcardOptionsRequest(Integer count, Boolean groupBySection, String section, String focus, String title) {}
}
