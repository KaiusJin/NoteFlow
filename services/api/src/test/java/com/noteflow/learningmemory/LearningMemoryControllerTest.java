package com.noteflow.learningmemory;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class LearningMemoryControllerTest {
    private LearningMemoryService memory;
    private MockMvc mvc;

    @BeforeEach void setup() {
        memory = Mockito.mock(LearningMemoryService.class);
        mvc = MockMvcBuilders.standaloneSetup(new LearningMemoryController(memory)).build();
    }

    @Test void collectsTypedEvent() throws Exception {
        when(memory.record(any())).thenReturn(Map.of("acceptedTopics", 1, "duplicateTopics", 0));
        mvc.perform(post("/learning-memory/events").contentType(MediaType.APPLICATION_JSON)
                .content("{\"eventId\":\"answer-1\",\"eventType\":\"QUESTION_ANSWERED\",\"topics\":[\"Covariance\"],\"correct\":false}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.acceptedTopics").value(1));
    }

    @Test void exposesCompactPlannerReads() throws Exception {
        when(memory.profile(any(), anyInt())).thenReturn(Map.of("topicCount", 1, "averageMastery", .42));
        when(memory.weakTopics(any(), anyInt())).thenReturn(List.of(Map.of("topic", "Covariance", "mastery", .42)));
        when(memory.dueReviews(any(), anyInt())).thenReturn(List.of(Map.of("topic", "Covariance")));
        mvc.perform(get("/learning-memory/profile")).andExpect(status().isOk()).andExpect(jsonPath("$.topicCount").value(1));
        mvc.perform(get("/learning-memory/weak-topics")).andExpect(status().isOk()).andExpect(jsonPath("$[0].topic").value("Covariance"));
        mvc.perform(get("/learning-memory/due-reviews")).andExpect(status().isOk()).andExpect(jsonPath("$[0].topic").value("Covariance"));
    }

    @Test void recordsExplicitFeedback() throws Exception {
        when(memory.recordFeedback(any())).thenReturn(Map.of("acceptedTopics", 1));
        mvc.perform(post("/learning-memory/feedback").contentType(MediaType.APPLICATION_JSON)
                .content("{\"eventId\":\"feedback-1\",\"topic\":\"Independence\",\"feedback\":\"CONFUSED\"}"))
            .andExpect(status().isOk()).andExpect(jsonPath("$.acceptedTopics").value(1));
    }
}
