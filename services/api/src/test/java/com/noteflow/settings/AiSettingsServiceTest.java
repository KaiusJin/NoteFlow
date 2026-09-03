package com.noteflow.settings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.noteflow.workspace.LocalWorkspaceService;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

class AiSettingsServiceTest {
    @Test
    void isolatesCachedProviderSecretsByAuthenticatedUser() {
        UUID firstUser = UUID.randomUUID();
        UUID secondUser = UUID.randomUUID();
        AtomicReference<UUID> activeUser = new AtomicReference<>(firstUser);
        LocalWorkspaceService users = mock(LocalWorkspaceService.class);
        AiSettingsRepository repository = mock(AiSettingsRepository.class);
        when(users.currentUserId()).thenAnswer(ignored -> activeUser.get());

        AiSettings first = new AiSettings(firstUser);
        first.setGeminiApiKey("first-secret");
        AiSettings second = new AiSettings(secondUser);
        second.setGeminiApiKey("second-secret");
        when(repository.findById(firstUser)).thenReturn(Optional.of(first));
        when(repository.findById(secondUser)).thenReturn(Optional.of(second));

        AiSettingsService service = new AiSettingsService(
            repository,
            users,
            "",
            "",
            "disabled",
            "gemini-embedding-001",
            "text-embedding-3-small"
        );

        assertThat(service.geminiApiKey()).isEqualTo("first-secret");
        activeUser.set(secondUser);
        assertThat(service.geminiApiKey()).isEqualTo("second-secret");
        activeUser.set(firstUser);
        assertThat(service.geminiApiKey()).isEqualTo("first-secret");
    }
}
