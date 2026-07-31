package com.noteflow.settings;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.noteflow.common.ApiExceptionHandler;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class AiSettingsControllerTest {
    private AiSettingsService service;
    private MockMvc mvc;

    @BeforeEach
    void setup() {
        service = Mockito.mock(AiSettingsService.class);
        AiSettings settings = new AiSettings(java.util.UUID.randomUUID());
        when(service.loadOrCreate()).thenReturn(settings);
        when(service.save(any(AiSettings.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(service.llmProvider()).thenReturn("gemini");
        when(service.embeddingProvider()).thenReturn("gemini");
        when(service.embeddingModel()).thenReturn("gemini-embedding-001");
        mvc = MockMvcBuilders.standaloneSetup(new AiSettingsController(service))
            .setControllerAdvice(new ApiExceptionHandler())
            .build();
    }

    @Test
    void acceptsValidModelIdentifiers() throws Exception {
        mvc.perform(put("/settings/ai")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "geminiLlmModel": "models/gemini-2.5-flash:latest",
                      "openaiEmbeddingModel": "text-embedding-3-small"
                    }
                    """))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.geminiLlmModel").value("models/gemini-2.5-flash:latest"))
            .andExpect(jsonPath("$.openaiEmbeddingModel").value("text-embedding-3-small"));
    }

    @Test
    void rejectsHtmlInModelIdentifier() throws Exception {
        mvc.perform(put("/settings/ai")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {"geminiLlmModel": "gemini\\"><img src=x onerror=alert(1)>"}
                    """))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("Model id")));
    }

    @Test
    void rejectsControlCharactersInApiKeys() throws Exception {
        mvc.perform(put("/settings/ai")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {"geminiApiKey": "secret\\nheader"}
                    """))
            .andExpect(status().isBadRequest())
            .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("control characters")));
    }
}
