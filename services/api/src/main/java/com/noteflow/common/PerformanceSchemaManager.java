package com.noteflow.common;

import jakarta.annotation.PostConstruct;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
public class PerformanceSchemaManager {
    private final JdbcTemplate jdbc;

    public PerformanceSchemaManager(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @PostConstruct
    public void ensureIndexes() {
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_documents_user_created ON documents(user_id, created_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_created ON tasks(user_id, created_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_status_created ON tasks(user_id, status, created_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_tasks_document_type_created ON tasks(document_id, task_type, created_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_updated ON notes(user_id, updated_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_notes_source_document_kind ON notes(source_document_id, source_kind, created_at)");
    }
}
