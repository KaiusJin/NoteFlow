package com.noteflow.learningmemory;

import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/** Creates the append-only event log and its incrementally maintained read models. */
@Component
@Order(200)
public class LearningMemorySchemaManager implements ApplicationRunner {
    private final JdbcTemplate jdbc;

    public LearningMemorySchemaManager(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Override @Transactional public void run(ApplicationArguments args) {
        jdbc.queryForObject("SELECT pg_advisory_xact_lock(hashtext('noteflow-learning-memory-schema-v1'))",Object.class);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_events (
              id UUID PRIMARY KEY,
              workspace_id UUID NOT NULL,
              scope_id UUID NOT NULL,
              external_event_id VARCHAR(256) NOT NULL,
              event_type VARCHAR(48) NOT NULL,
              topic_key VARCHAR(512) NOT NULL,
              topic TEXT NOT NULL,
              document_id UUID,
              artifact_type VARCHAR(32),
              artifact_id UUID,
              correct BOOLEAN,
              difficulty VARCHAR(16),
              response_time_ms INTEGER,
              hint_used BOOLEAN NOT NULL DEFAULT FALSE,
              review_grade VARCHAR(16),
              mistake_type VARCHAR(48),
              mistake_summary TEXT,
              metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              occurred_at TIMESTAMPTZ NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(workspace_id, external_event_id, topic_key)
            )
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS topic_learning_memory (
              workspace_id UUID NOT NULL,
              scope_id UUID NOT NULL,
              topic_key VARCHAR(512) NOT NULL,
              topic TEXT NOT NULL,
              mastery DOUBLE PRECISION NOT NULL DEFAULT 0.5,
              confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
              evidence_weight DOUBLE PRECISION NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0,
              correct_count INTEGER NOT NULL DEFAULT 0,
              incorrect_count INTEGER NOT NULL DEFAULT 0,
              hint_count INTEGER NOT NULL DEFAULT 0,
              total_response_time_ms BIGINT NOT NULL DEFAULT 0,
              consecutive_correct INTEGER NOT NULL DEFAULT 0,
              consecutive_incorrect INTEGER NOT NULL DEFAULT 0,
              recent_trend DOUBLE PRECISION NOT NULL DEFAULT 0,
              last_activity_at TIMESTAMPTZ,
              last_reviewed_at TIMESTAMPTZ,
              next_review_at TIMESTAMPTZ,
              needs_review BOOLEAN NOT NULL DEFAULT FALSE,
              version BIGINT NOT NULL DEFAULT 1,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY(workspace_id, scope_id, topic_key)
            )
            """);
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS mistake_memory (
              workspace_id UUID NOT NULL,
              scope_id UUID NOT NULL,
              topic_key VARCHAR(512) NOT NULL,
              mistake_fingerprint VARCHAR(128) NOT NULL,
              topic TEXT NOT NULL,
              mistake_type VARCHAR(48) NOT NULL,
              summary TEXT NOT NULL,
              occurrences INTEGER NOT NULL DEFAULT 1,
              first_seen_at TIMESTAMPTZ NOT NULL,
              last_seen_at TIMESTAMPTZ NOT NULL,
              last_event_id UUID NOT NULL,
              version BIGINT NOT NULL DEFAULT 1,
              PRIMARY KEY(workspace_id, scope_id, topic_key, mistake_fingerprint)
            )
            """);
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_learning_events_workspace_time ON learning_events(workspace_id, occurred_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_learning_events_artifact ON learning_events(workspace_id, artifact_type, artifact_id)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_topic_memory_weak ON topic_learning_memory(workspace_id, needs_review, mastery, next_review_at)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_topic_memory_due ON topic_learning_memory(workspace_id, next_review_at) WHERE needs_review");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_mistake_memory_rank ON mistake_memory(workspace_id, occurrences DESC, last_seen_at DESC)");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS lapse_count INTEGER NOT NULL DEFAULT 0");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS stability_days DOUBLE PRECISION NOT NULL DEFAULT 1");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS calibration_error DOUBLE PRECISION NOT NULL DEFAULT 0");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS easy_attempts INTEGER NOT NULL DEFAULT 0");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS medium_attempts INTEGER NOT NULL DEFAULT 0");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS hard_attempts INTEGER NOT NULL DEFAULT 0");
        jdbc.execute("ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS response_time_count INTEGER NOT NULL DEFAULT 0");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_memory_history (
          id UUID PRIMARY KEY,workspace_id UUID NOT NULL,scope_id UUID NOT NULL,topic_key VARCHAR(512) NOT NULL,
          source_event_id UUID,mastery DOUBLE PRECISION NOT NULL,confidence DOUBLE PRECISION NOT NULL,
          attempts INTEGER NOT NULL,recent_trend DOUBLE PRECISION NOT NULL,algorithm_version VARCHAR(32) NOT NULL,
          recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_goals (
          id UUID PRIMARY KEY,workspace_id UUID NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
          deadline TIMESTAMPTZ,priority INTEGER NOT NULL DEFAULT 50,status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
          topic_keys_json JSONB NOT NULL DEFAULT '[]'::jsonb,document_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
          version BIGINT NOT NULL DEFAULT 1,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_preferences (
          workspace_id UUID NOT NULL,preference_key VARCHAR(128) NOT NULL,value_json JSONB NOT NULL,
          source VARCHAR(16) NOT NULL,confidence DOUBLE PRECISION NOT NULL,evidence_count INTEGER NOT NULL DEFAULT 1,
          version BIGINT NOT NULL DEFAULT 1,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY(workspace_id,preference_key))""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_artifact_links (
          workspace_id UUID NOT NULL,topic_key VARCHAR(512) NOT NULL,artifact_type VARCHAR(32) NOT NULL,
          artifact_id UUID NOT NULL,title TEXT NOT NULL DEFAULT '',document_id UUID,status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
          interaction_count INTEGER NOT NULL DEFAULT 0,last_interacted_at TIMESTAMPTZ,
          metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(workspace_id,topic_key,artifact_type,artifact_id))""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_topic_edges (
          workspace_id UUID NOT NULL,from_topic_key VARCHAR(512) NOT NULL,to_topic_key VARCHAR(512) NOT NULL,
          relation VARCHAR(32) NOT NULL,weight DOUBLE PRECISION NOT NULL DEFAULT .5,source VARCHAR(32) NOT NULL,
          evidence_count INTEGER NOT NULL DEFAULT 1,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY(workspace_id,from_topic_key,to_topic_key,relation))""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_memory_corrections (
          id UUID PRIMARY KEY,workspace_id UUID NOT NULL,scope_id UUID NOT NULL,topic_key VARCHAR(512) NOT NULL,
          correction_type VARCHAR(24) NOT NULL,old_value_json JSONB NOT NULL,new_value_json JSONB NOT NULL,
          reason TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_strategy_experiments (
          workspace_id UUID NOT NULL,experiment_key VARCHAR(128) NOT NULL,variant VARCHAR(64) NOT NULL,
          assignment_hash VARCHAR(64) NOT NULL,outcome_sum DOUBLE PRECISION NOT NULL DEFAULT 0,
          outcome_count INTEGER NOT NULL DEFAULT 0,assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(workspace_id,experiment_key))""");
        jdbc.execute("""
            CREATE TABLE IF NOT EXISTS learning_study_plans (
          id UUID PRIMARY KEY,workspace_id UUID NOT NULL,title TEXT NOT NULL,goal_id UUID,
          plan_json JSONB NOT NULL,status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',estimated_minutes INTEGER NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_learning_history_topic ON learning_memory_history(workspace_id,topic_key,recorded_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_learning_goals_active ON learning_goals(workspace_id,status,deadline,priority DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_learning_artifacts_topic ON learning_artifact_links(workspace_id,topic_key,status,last_interacted_at DESC)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_learning_edges_from ON learning_topic_edges(workspace_id,from_topic_key,weight DESC)");
    }
}
