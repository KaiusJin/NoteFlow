package com.noteflow.conversations;

import jakarta.annotation.PostConstruct;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

@Component
public class ConversationSchemaManager {
    private final JdbcTemplate jdbc;

    public ConversationSchemaManager(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @PostConstruct
    public void ensureSchema() {
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS rag_conversations (
                id UUID PRIMARY KEY,
                user_id UUID NOT NULL,
                title TEXT,
                status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                active_summary TEXT,
                active_summary_json TEXT,
                summary_version INTEGER NOT NULL DEFAULT 0,
                summary_token_count INTEGER NOT NULL DEFAULT 0,
                summary_covers_through_at TIMESTAMPTZ,
                summary_covers_through_message_id UUID,
                extraction_covers_through_at TIMESTAMPTZ,
                extraction_covers_through_message_id UUID,
                selected_pdf_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                selected_ai_note_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                last_message_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS rag_messages (
                id UUID PRIMARY KEY,
                conversation_id UUID NOT NULL REFERENCES rag_conversations(id) ON DELETE CASCADE,
                role VARCHAR(32) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED',
                content_markdown TEXT NOT NULL DEFAULT '',
                token_count INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT,
                model_provider TEXT,
                model_name TEXT,
                structured_response_json TEXT,
                error_message TEXT,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """);
        jdbc.execute("ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS selected_pdf_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb");
        jdbc.execute("ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS selected_ai_note_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb");
        jdbc.execute("ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS summary_covers_through_at TIMESTAMPTZ");
        jdbc.execute("ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS summary_covers_through_message_id UUID");
        jdbc.execute("ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS extraction_covers_through_at TIMESTAMPTZ");
        jdbc.execute("ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS extraction_covers_through_message_id UUID");
        jdbc.execute("ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS model_provider TEXT");
        jdbc.execute("ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS model_name TEXT");
        jdbc.execute("ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS structured_response_json TEXT");
        jdbc.execute("ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS error_message TEXT");
        jdbc.execute("ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS rag_message_citations (
                id UUID PRIMARY KEY,
                message_id UUID NOT NULL REFERENCES rag_messages(id) ON DELETE CASCADE,
                citation_index INTEGER NOT NULL,
                source_domain VARCHAR(32) NOT NULL,
                source_object_type VARCHAR(64) NOT NULL,
                source_object_ids JSONB NOT NULL,
                document_id UUID NOT NULL,
                page_start INTEGER,
                page_end INTEGER,
                source_title VARCHAR(500),
                evidence_snapshot TEXT NOT NULL,
                retrieval_score DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(message_id, citation_index)
            )
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS conversation_task_targets (
                task_id UUID PRIMARY KEY,
                conversation_id UUID NOT NULL,
                message_id UUID NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS agent_run_steps (
                id UUID PRIMARY KEY,
                message_id UUID NOT NULL REFERENCES rag_messages(id) ON DELETE CASCADE,
                step_index INTEGER NOT NULL,
                thought TEXT,
                action_type VARCHAR(32) NOT NULL,
                tool VARCHAR(128),
                args_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                observation TEXT NOT NULL,
                ok BOOLEAN NOT NULL DEFAULT TRUE,
                tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                handle_json JSONB,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(message_id, step_index)
            )
            """);
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_rag_conversations_user_updated ON rag_conversations(user_id, updated_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_rag_messages_conversation_created ON rag_messages(conversation_id, created_at)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_rag_citations_message ON rag_message_citations(message_id, citation_index)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_agent_run_steps_message ON agent_run_steps(message_id, step_index)");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS agent_run_snapshots (
              message_id UUID PRIMARY KEY REFERENCES rag_messages(id) ON DELETE CASCADE,
              conversation_id UUID NOT NULL,user_id UUID NOT NULL,question TEXT NOT NULL,
              status VARCHAR(24) NOT NULL,state_json JSONB NOT NULL,waiting_task_id UUID,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS agent_task_waits (
              task_id UUID NOT NULL,message_id UUID NOT NULL REFERENCES rag_messages(id) ON DELETE CASCADE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(task_id,message_id))
            """);
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_agent_task_waits_task ON agent_task_waits(task_id)");
        jdbc.execute("ALTER TABLE tasks ALTER COLUMN document_id DROP NOT NULL");
        // Study generation channels (SECTION vs AGENT). The worker owns the
        // study tables; guard so the API can still boot on a fresh database.
        jdbc.execute("""
            DO $$ BEGIN
              IF to_regclass('flashcard_decks') IS NOT NULL THEN
                ALTER TABLE flashcard_decks ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'SECTION';
                ALTER TABLE flashcard_decks ADD COLUMN IF NOT EXISTS source_scope_json TEXT NOT NULL DEFAULT '{}';
              END IF;
              IF to_regclass('quiz_sets') IS NOT NULL THEN
                ALTER TABLE quiz_sets ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'SECTION';
                ALTER TABLE quiz_sets ADD COLUMN IF NOT EXISTS source_scope_json TEXT NOT NULL DEFAULT '{}';
              END IF;
            END $$
            """);
        ensureTaskConstraint(
            "tasks_task_type_check",
            "RESUME_AGENT_RUN",
            "task_type IN ('PARSE_DOCUMENT','GENERATE_EMBEDDINGS','GENERATE_NOTES','GENERATE_FLASHCARDS','GENERATE_QUIZ','GRADE_QUIZ_ATTEMPT','ANSWER_CONVERSATION_TURN','RESUME_AGENT_RUN','MAINTAIN_CONVERSATION_MEMORY','ASK_DOCUMENT','EXPORT_MARKDOWN')"
        );
        ensureTaskConstraint(
            "tasks_current_step_check",
            "AGENT_FALLBACK",
            "current_step IN ('UPLOADED','PARSING_PDF','EXTRACTING_TEXT','ANALYZING_VISUAL_CONTENT','CROPPING_VISUAL_REGIONS','VLM_ANALYSIS','LAYOUT_CHUNKING','CHUNKING','GENERATING_EMBEDDINGS','GENERATING_NOTES','GENERATING_FLASHCARDS','GENERATING_QUIZ','GRADING_QUIZ','ANSWERING','AGENT_PLANNING','AGENT_TOOL','AGENT_FINALIZING','AGENT_FALLBACK','MAINTAINING_MEMORY','COMPLETED','FAILED')"
        );
    }

    private void ensureTaskConstraint(String name, String sentinel, String expression) {
        List<String> definitions = jdbc.queryForList(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='tasks'::regclass AND conname=?",
            String.class, name);
        if (!definitions.isEmpty() && definitions.get(0).contains(sentinel)) return;
        jdbc.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS " + name);
        jdbc.execute("ALTER TABLE tasks ADD CONSTRAINT " + name + " CHECK (" + expression + ")");
    }
}
