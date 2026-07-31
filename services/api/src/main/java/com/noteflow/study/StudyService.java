package com.noteflow.study;

import com.noteflow.documents.DocumentRepository;
import com.noteflow.learningmemory.LearningMemoryService;
import com.noteflow.tasks.TaskDispatchService;
import com.noteflow.workspace.LocalWorkspaceService;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

@Service
public class StudyService {
    private final FlashcardStudyService flashcards;
    private final QuizStudyService quizzes;

    public StudyService(LocalWorkspaceService users, DocumentRepository documents, TaskDispatchService tasks,
            JdbcTemplate jdbc, LearningMemoryService memory) {
        this(
            new FlashcardStudyService(users, documents, jdbc, memory),
            new QuizStudyService(users, documents, tasks, jdbc, memory)
        );
    }

    StudyService(FlashcardStudyService flashcards, QuizStudyService quizzes) {
        this.flashcards = flashcards;
        this.quizzes = quizzes;
    }

    public List<Map<String, Object>> decks(UUID documentId) { return flashcards.decks(documentId); }
    public List<Map<String, Object>> cards(UUID deckId) { return flashcards.cards(deckId); }
    public List<Map<String, Object>> dueCards(UUID deckId, int limit) { return flashcards.dueCards(deckId, limit); }
    public Map<String, Object> review(UUID cardId, String grade) { return flashcards.review(cardId, grade); }
    public Map<String, Object> review(UUID cardId, String grade, String eventId) { return flashcards.review(cardId, grade, eventId); }
    public List<Map<String, Object>> quizzes(UUID documentId) { return quizzes.quizzes(documentId); }
    public List<Map<String, Object>> questions(UUID quizId) { return quizzes.questions(quizId); }
    public Map<String, Object> startAttempt(UUID quizId) { return quizzes.startAttempt(quizId); }
    public Map<String, Object> saveAnswer(UUID attemptId, UUID questionId, String response, Integer responseTimeMs, boolean hintUsed) {
        return quizzes.saveAnswer(attemptId, questionId, response, responseTimeMs, hintUsed);
    }
    public Map<String, Object> saveAnswers(UUID attemptId, List<StudyController.BatchAnswerRequest> answers) {
        return quizzes.saveAnswers(attemptId, answers);
    }
    public Map<String, Object> submit(UUID attemptId) { return quizzes.submit(attemptId); }
    public Map<String, Object> attemptResult(UUID attemptId) { return quizzes.attemptResult(attemptId); }
}
