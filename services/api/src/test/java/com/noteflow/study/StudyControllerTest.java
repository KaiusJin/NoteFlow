package com.noteflow.study;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class StudyControllerTest {
    private StudyService service;
    private MockMvc mvc;
    private final UUID id = UUID.randomUUID();

    @BeforeEach void setup() {
        service = Mockito.mock(StudyService.class);
        mvc = MockMvcBuilders.standaloneSetup(new StudyController(service)).build();
    }

    @Test void listsDecks() throws Exception {
        when(service.decks(id)).thenReturn(List.of(Map.of("id", id, "status", "READY")));
        mvc.perform(get("/documents/{id}/flashcard-decks", id))
            .andExpect(status().isOk()).andExpect(jsonPath("$[0].status").value("READY"));
    }

    @Test void startsQuizAttempt() throws Exception {
        when(service.startAttempt(id)).thenReturn(Map.of("attemptId", id, "status", "IN_PROGRESS"));
        mvc.perform(post("/quiz-sets/{id}/attempts", id))
            .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("IN_PROGRESS"));
    }

    @Test void acceptsReviewGrade() throws Exception {
        when(service.review(id, "GOOD")).thenReturn(Map.of("status", "LEARNING", "intervalDays", 1));
        mvc.perform(post("/flashcards/{id}/reviews", id).contentType(MediaType.APPLICATION_JSON)
            .content("{\"grade\":\"GOOD\"}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.intervalDays").value(1));
    }
}
