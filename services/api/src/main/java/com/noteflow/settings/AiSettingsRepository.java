package com.noteflow.settings;

import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AiSettingsRepository extends JpaRepository<AiSettings, UUID> {
}
