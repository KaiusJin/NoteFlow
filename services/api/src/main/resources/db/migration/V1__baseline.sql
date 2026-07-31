--
-- PostgreSQL database dump
--

-- Dumped from database version 16.14 (Debian 16.14-1.pgdg12+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: cleanup_study_generation_checkpoints(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_study_generation_checkpoints() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
              BEGIN DELETE FROM study_generation_checkpoints WHERE set_id=OLD.id; RETURN OLD; END;
              $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: agent_run_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_run_snapshots (
    message_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    user_id uuid NOT NULL,
    question text NOT NULL,
    status character varying(24) NOT NULL,
    state_json jsonb NOT NULL,
    waiting_task_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_run_steps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_run_steps (
    id uuid NOT NULL,
    message_id uuid NOT NULL,
    step_index integer NOT NULL,
    thought text,
    action_type character varying(32) NOT NULL,
    tool character varying(128),
    args_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    observation text NOT NULL,
    ok boolean DEFAULT true NOT NULL,
    tokens integer DEFAULT 0 NOT NULL,
    latency_ms integer DEFAULT 0 NOT NULL,
    handle_json jsonb,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_task_waits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_task_waits (
    task_id uuid NOT NULL,
    message_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: conversation_task_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_task_targets (
    task_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    message_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_ai_note_sections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_ai_note_sections (
    id uuid NOT NULL,
    confidence double precision,
    created_at timestamp(6) with time zone,
    document_id uuid,
    heading character varying(255),
    markdown text,
    metadata_json text,
    note_id uuid,
    page_end integer,
    page_start integer,
    section_index integer NOT NULL,
    section_type character varying(255),
    source_chunk_ids_json text,
    source_pages_json text,
    warnings_json text
);


--
-- Name: document_ai_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_ai_notes (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    document_id uuid,
    markdown text,
    metadata_json text,
    model_name character varying(255),
    model_provider character varying(255),
    note_version integer NOT NULL,
    prompt_version character varying(255),
    quality_report_json text,
    source_document_version character varying(255),
    status character varying(255),
    summary text,
    title character varying(255),
    updated_at timestamp(6) with time zone
);


--
-- Name: document_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_chunks (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    section_title character varying(255),
    chunk_index integer NOT NULL,
    content text NOT NULL,
    token_count integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    page_start integer,
    page_end integer,
    chunk_type character varying(255),
    source_asset_id uuid,
    metadata_json text
);


--
-- Name: document_editable_notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_editable_notes (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    document_id uuid,
    markdown text,
    source_kind character varying(255),
    title character varying(255),
    updated_at timestamp(6) with time zone,
    user_id uuid
);


--
-- Name: document_embeddings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_embeddings (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    source_table character varying(64),
    source_id uuid,
    content_kind character varying(64),
    provider character varying(64),
    model character varying(128),
    embedding public.vector,
    embedding_text text,
    metadata_json text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_domain character varying(32),
    source_object_type character varying(64),
    source_object_id uuid,
    embedding_provider character varying(64),
    embedding_model character varying(128),
    embedding_dimension integer,
    content_hash character varying(128),
    text_preview text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS ((setweight(to_tsvector('simple'::regconfig, COALESCE(embedding_text, ''::text)), 'A'::"char") || setweight(to_tsvector('simple'::regconfig, COALESCE(text_preview, ''::text)), 'B'::"char"))) STORED,
    exact_search_text text GENERATED ALWAYS AS (lower(regexp_replace(translate(COALESCE(embedding_text, ''::text), '[]{}'::text, '()()'::text), '[[:space:]]+'::text, ''::text, 'g'::text))) STORED
);


--
-- Name: document_layout_blocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_layout_blocks (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    block_index integer NOT NULL,
    block_type character varying(255) NOT NULL,
    content text,
    bbox_json text,
    section_title character varying(255),
    heading_path_json text,
    source_asset_id uuid,
    confidence double precision,
    metadata_json text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_markdown_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_markdown_documents (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    document_id uuid,
    markdown text,
    quality_report_json text,
    structure_json text,
    updated_at timestamp(6) with time zone
);


--
-- Name: document_markdown_pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_markdown_pages (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    document_id uuid,
    markdown text,
    page_number integer NOT NULL,
    quality_score double precision NOT NULL,
    source_type character varying(255),
    structure_json text,
    updated_at timestamp(6) with time zone,
    warnings_json text
);


--
-- Name: document_page_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_page_assets (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    asset_type character varying(255) NOT NULL,
    image_path character varying(255) NOT NULL,
    width integer NOT NULL,
    height integer NOT NULL,
    image_count integer NOT NULL,
    drawing_count integer NOT NULL,
    image_coverage double precision NOT NULL,
    text_length integer NOT NULL,
    visual_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_parse_manifests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_parse_manifests (
    document_id uuid NOT NULL,
    manifest_json text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_parse_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_parse_results (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    parser_name character varying(255) NOT NULL,
    page_count integer NOT NULL,
    extracted_text_length integer NOT NULL,
    extracted_text_preview text,
    detected_content_source_type character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_confidence double precision,
    source_distribution_json text
);


--
-- Name: document_visual_regions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_visual_regions (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    region_index integer NOT NULL,
    region_type character varying(255) NOT NULL,
    asset_path character varying(255) NOT NULL,
    bbox_json text,
    page_asset_id uuid,
    width integer NOT NULL,
    height integer NOT NULL,
    confidence double precision NOT NULL,
    metadata_json text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: document_vlm_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_vlm_results (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    page_number integer NOT NULL,
    region_index integer NOT NULL,
    region_type character varying(255) NOT NULL,
    provider character varying(255) NOT NULL,
    model character varying(255) NOT NULL,
    transcription text,
    description text,
    latex text,
    code text,
    uncertainty text,
    search_text text,
    raw_response_json text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    input_fingerprint character varying(64),
    attempt_count integer DEFAULT 1 NOT NULL,
    content_kind character varying(255),
    importance character varying(255),
    reading_order text,
    language character varying(255)
);


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid NOT NULL,
    content_source_type character varying(255),
    created_at timestamp(6) with time zone,
    document_type character varying(255),
    file_size bigint NOT NULL,
    file_type character varying(255),
    language character varying(255),
    original_filename character varying(255),
    page_count integer,
    status character varying(255),
    storage_path character varying(255),
    title character varying(255),
    updated_at timestamp(6) with time zone,
    user_id uuid,
    CONSTRAINT documents_content_source_type_check CHECK (((content_source_type)::text = ANY ((ARRAY['TEXT_PDF'::character varying, 'SCANNED_PDF'::character varying, 'HANDWRITTEN_SCAN'::character varying, 'MIXED'::character varying, 'UNKNOWN'::character varying])::text[]))),
    CONSTRAINT documents_document_type_check CHECK (((document_type)::text = ANY ((ARRAY['COURSE_NOTES'::character varying, 'LECTURE_SLIDES'::character varying, 'RESEARCH_PAPER'::character varying, 'TEXTBOOK_CHAPTER'::character varying, 'ASSIGNMENT'::character varying, 'PAST_EXAM'::character varying, 'HANDWRITTEN_NOTES'::character varying, 'OTHER'::character varying])::text[]))),
    CONSTRAINT documents_status_check CHECK (((status)::text = ANY ((ARRAY['UPLOADED'::character varying, 'PROCESSING'::character varying, 'READY'::character varying, 'FAILED'::character varying, 'DELETED'::character varying])::text[])))
);


--
-- Name: flashcard_decks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flashcard_decks (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    user_id uuid NOT NULL,
    version integer NOT NULL,
    title text NOT NULL,
    source_scope character varying(32) DEFAULT 'WHOLE_DOCUMENT'::character varying NOT NULL,
    status character varying(32) DEFAULT 'GENERATING'::character varying NOT NULL,
    generation_options_json text DEFAULT '{}'::text NOT NULL,
    provider character varying(64),
    model character varying(128),
    prompt_version character varying(64),
    total_source_groups integer DEFAULT 0 NOT NULL,
    completed_source_groups integer DEFAULT 0 NOT NULL,
    quality_report_json text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    origin character varying(16) DEFAULT 'SECTION'::character varying NOT NULL,
    source_scope_json text DEFAULT '{}'::text NOT NULL
);


--
-- Name: flashcard_review_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flashcard_review_states (
    user_id uuid NOT NULL,
    flashcard_id uuid NOT NULL,
    status character varying(16) DEFAULT 'NEW'::character varying NOT NULL,
    ease_factor double precision DEFAULT 2.5 NOT NULL,
    interval_days integer DEFAULT 0 NOT NULL,
    repetitions integer DEFAULT 0 NOT NULL,
    due_at timestamp with time zone,
    last_reviewed_at timestamp with time zone,
    last_grade character varying(16),
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: flashcards; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.flashcards (
    id uuid NOT NULL,
    deck_id uuid NOT NULL,
    document_id uuid NOT NULL,
    source_group_index integer NOT NULL,
    item_index integer NOT NULL,
    card_type character varying(32) NOT NULL,
    front text NOT NULL,
    back text NOT NULL,
    cloze_text text DEFAULT ''::text NOT NULL,
    difficulty character varying(16) NOT NULL,
    topic text NOT NULL,
    hint text DEFAULT ''::text NOT NULL,
    tags_json text DEFAULT '[]'::text NOT NULL,
    source_chunk_ids_json text NOT NULL,
    source_pages_json text NOT NULL,
    dedupe_hash character varying(64) NOT NULL,
    confidence double precision NOT NULL,
    warnings_json text DEFAULT '[]'::text NOT NULL,
    metadata_json text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: folders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.folders (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    name character varying(255),
    parent_id uuid,
    updated_at timestamp(6) with time zone,
    user_id uuid
);


--
-- Name: learning_artifact_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_artifact_links (
    workspace_id uuid NOT NULL,
    topic_key character varying(512) NOT NULL,
    artifact_type character varying(32) NOT NULL,
    artifact_id uuid NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    document_id uuid,
    status character varying(24) DEFAULT 'ACTIVE'::character varying NOT NULL,
    interaction_count integer DEFAULT 0 NOT NULL,
    last_interacted_at timestamp with time zone,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_events (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    external_event_id character varying(256) NOT NULL,
    event_type character varying(48) NOT NULL,
    topic_key character varying(512) NOT NULL,
    topic text NOT NULL,
    document_id uuid,
    artifact_type character varying(32),
    artifact_id uuid,
    correct boolean,
    difficulty character varying(16),
    response_time_ms integer,
    hint_used boolean DEFAULT false NOT NULL,
    review_grade character varying(16),
    mistake_type character varying(48),
    mistake_summary text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_goals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_goals (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    title text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    deadline timestamp with time zone,
    priority integer DEFAULT 50 NOT NULL,
    status character varying(16) DEFAULT 'ACTIVE'::character varying NOT NULL,
    topic_keys_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    document_ids_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_memory_corrections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_memory_corrections (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    topic_key character varying(512) NOT NULL,
    correction_type character varying(24) NOT NULL,
    old_value_json jsonb NOT NULL,
    new_value_json jsonb NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_memory_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_memory_history (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    topic_key character varying(512) NOT NULL,
    source_event_id uuid,
    mastery double precision NOT NULL,
    confidence double precision NOT NULL,
    attempts integer NOT NULL,
    recent_trend double precision NOT NULL,
    algorithm_version character varying(32) NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_preferences (
    workspace_id uuid NOT NULL,
    preference_key character varying(128) NOT NULL,
    value_json jsonb NOT NULL,
    source character varying(16) NOT NULL,
    confidence double precision NOT NULL,
    evidence_count integer DEFAULT 1 NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_strategy_experiments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_strategy_experiments (
    workspace_id uuid NOT NULL,
    experiment_key character varying(128) NOT NULL,
    variant character varying(64) NOT NULL,
    assignment_hash character varying(64) NOT NULL,
    outcome_sum double precision DEFAULT 0 NOT NULL,
    outcome_count integer DEFAULT 0 NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_study_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_study_plans (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    title text NOT NULL,
    goal_id uuid,
    plan_json jsonb NOT NULL,
    status character varying(16) DEFAULT 'ACTIVE'::character varying NOT NULL,
    estimated_minutes integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: learning_topic_edges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.learning_topic_edges (
    workspace_id uuid NOT NULL,
    from_topic_key character varying(512) NOT NULL,
    to_topic_key character varying(512) NOT NULL,
    relation character varying(32) NOT NULL,
    weight double precision DEFAULT 0.5 NOT NULL,
    source character varying(32) NOT NULL,
    evidence_count integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: mistake_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mistake_memory (
    workspace_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    topic_key character varying(512) NOT NULL,
    mistake_fingerprint character varying(128) NOT NULL,
    topic text NOT NULL,
    mistake_type character varying(48) NOT NULL,
    summary text NOT NULL,
    occurrences integer DEFAULT 1 NOT NULL,
    first_seen_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    last_event_id uuid NOT NULL,
    version bigint DEFAULT 1 NOT NULL
);


--
-- Name: notes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notes (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    folder_id uuid,
    markdown text,
    source_document_id uuid,
    source_kind character varying(255),
    title character varying(255),
    updated_at timestamp(6) with time zone,
    user_id uuid
);


--
-- Name: quiz_answers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quiz_answers (
    id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    question_id uuid NOT NULL,
    user_response text DEFAULT ''::text NOT NULL,
    is_correct boolean,
    awarded_points double precision,
    feedback text,
    key_points_hit_json text,
    graded_by character varying(16),
    grading_error text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    response_time_ms integer,
    hint_used boolean DEFAULT false NOT NULL
);


--
-- Name: quiz_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quiz_attempts (
    id uuid NOT NULL,
    quiz_set_id uuid NOT NULL,
    user_id uuid NOT NULL,
    status character varying(32) DEFAULT 'IN_PROGRESS'::character varying NOT NULL,
    score double precision DEFAULT 0 NOT NULL,
    max_score double precision DEFAULT 0 NOT NULL,
    weak_topics_json text DEFAULT '[]'::text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    submitted_at timestamp with time zone,
    completed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    grading_usage_json text DEFAULT '{}'::text NOT NULL
);


--
-- Name: quiz_questions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quiz_questions (
    id uuid NOT NULL,
    quiz_set_id uuid NOT NULL,
    document_id uuid NOT NULL,
    source_group_index integer NOT NULL,
    item_index integer NOT NULL,
    question_type character varying(32) NOT NULL,
    difficulty character varying(16) NOT NULL,
    topic text NOT NULL,
    stem text NOT NULL,
    options_json text DEFAULT '[]'::text NOT NULL,
    correct_answer text NOT NULL,
    answer_key text NOT NULL,
    rubric_json text NOT NULL,
    explanation text NOT NULL,
    related_formula text DEFAULT ''::text NOT NULL,
    common_mistake text DEFAULT ''::text NOT NULL,
    distractor_rationale_json text DEFAULT '[]'::text NOT NULL,
    points double precision NOT NULL,
    source_chunk_ids_json text NOT NULL,
    source_pages_json text NOT NULL,
    dedupe_hash character varying(64) NOT NULL,
    confidence double precision NOT NULL,
    warnings_json text DEFAULT '[]'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: quiz_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quiz_sets (
    id uuid NOT NULL,
    document_id uuid NOT NULL,
    user_id uuid NOT NULL,
    version integer NOT NULL,
    title text NOT NULL,
    source_scope character varying(32) DEFAULT 'WHOLE_DOCUMENT'::character varying NOT NULL,
    status character varying(32) DEFAULT 'GENERATING'::character varying NOT NULL,
    difficulty_distribution_json text NOT NULL,
    generation_options_json text DEFAULT '{}'::text NOT NULL,
    provider character varying(64),
    model character varying(128),
    prompt_version character varying(64),
    total_source_groups integer DEFAULT 0 NOT NULL,
    completed_source_groups integer DEFAULT 0 NOT NULL,
    quality_report_json text,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    origin character varying(16) DEFAULT 'SECTION'::character varying NOT NULL,
    source_scope_json text DEFAULT '{}'::text NOT NULL
);


--
-- Name: rag_conversation_summaries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rag_conversation_summaries (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    version integer NOT NULL,
    summary_text text NOT NULL,
    summary_json text,
    token_count integer DEFAULT 0 NOT NULL,
    covered_message_count integer DEFAULT 0 NOT NULL,
    covers_through_at timestamp with time zone,
    covers_through_message_id uuid,
    provider character varying(64),
    model character varying(128),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rag_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rag_conversations (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    title character varying(300),
    status character varying(32) DEFAULT 'ACTIVE'::character varying NOT NULL,
    active_summary text,
    active_summary_json text,
    summary_version integer DEFAULT 0 NOT NULL,
    summary_token_count integer DEFAULT 0 NOT NULL,
    summary_covers_through_at timestamp with time zone,
    summary_covers_through_message_id uuid,
    extraction_covers_through_at timestamp with time zone,
    extraction_covers_through_message_id uuid,
    last_message_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    selected_pdf_document_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    selected_ai_note_document_ids jsonb DEFAULT '[]'::jsonb NOT NULL
);


--
-- Name: rag_memories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rag_memories (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    conversation_id uuid,
    memory_type character varying(32) NOT NULL,
    content text NOT NULL,
    content_hash character varying(128) NOT NULL,
    confidence double precision NOT NULL,
    status character varying(32) DEFAULT 'ACTIVE'::character varying NOT NULL,
    source_message_id uuid,
    superseded_by uuid,
    embedding public.vector,
    embedding_provider character varying(64),
    embedding_model character varying(128),
    embedding_dimension integer,
    access_count integer DEFAULT 0 NOT NULL,
    last_accessed_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rag_message_citations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rag_message_citations (
    id uuid NOT NULL,
    message_id uuid NOT NULL,
    citation_index integer NOT NULL,
    source_domain character varying(32) NOT NULL,
    source_object_type character varying(64) NOT NULL,
    source_object_ids jsonb NOT NULL,
    document_id uuid NOT NULL,
    page_start integer,
    page_end integer,
    source_title character varying(500),
    evidence_snapshot text NOT NULL,
    retrieval_score double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: rag_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rag_messages (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    role character varying(32) NOT NULL,
    status character varying(32) DEFAULT 'COMPLETED'::character varying NOT NULL,
    content_markdown text,
    token_count integer DEFAULT 0 NOT NULL,
    metadata_json text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    model_provider character varying(64),
    model_name character varying(128),
    structured_response_json text,
    error_message text,
    completed_at timestamp with time zone
);


--
-- Name: rag_user_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rag_user_preferences (
    user_id uuid NOT NULL,
    preference_key character varying(64) NOT NULL,
    preference_value character varying(400) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: study_execution_leases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.study_execution_leases (
    lease_key text NOT NULL,
    holder_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: study_generation_checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.study_generation_checkpoints (
    generation_type character varying(32) NOT NULL,
    set_id uuid NOT NULL,
    source_group_index integer NOT NULL,
    status character varying(16) NOT NULL,
    produced_count integer DEFAULT 0 NOT NULL,
    error_message text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: study_task_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.study_task_targets (
    task_id uuid NOT NULL,
    attempt_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    target_id uuid
);


--
-- Name: tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tasks (
    id uuid NOT NULL,
    completed_at timestamp(6) with time zone,
    created_at timestamp(6) with time zone,
    current_step character varying(255),
    document_id uuid,
    error_message text,
    progress integer NOT NULL,
    retry_count integer NOT NULL,
    started_at timestamp(6) with time zone,
    status character varying(255),
    task_type character varying(255),
    updated_at timestamp(6) with time zone,
    user_id uuid,
    priority integer,
    CONSTRAINT tasks_current_step_check CHECK (((current_step)::text = ANY ((ARRAY['UPLOADED'::character varying, 'PARSING_PDF'::character varying, 'EXTRACTING_TEXT'::character varying, 'ANALYZING_VISUAL_CONTENT'::character varying, 'CROPPING_VISUAL_REGIONS'::character varying, 'VLM_ANALYSIS'::character varying, 'LAYOUT_CHUNKING'::character varying, 'CHUNKING'::character varying, 'GENERATING_EMBEDDINGS'::character varying, 'GENERATING_NOTES'::character varying, 'GENERATING_FLASHCARDS'::character varying, 'GENERATING_QUIZ'::character varying, 'GRADING_QUIZ'::character varying, 'ANSWERING'::character varying, 'AGENT_PLANNING'::character varying, 'AGENT_TOOL'::character varying, 'AGENT_FINALIZING'::character varying, 'AGENT_FALLBACK'::character varying, 'MAINTAINING_MEMORY'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[]))),
    CONSTRAINT tasks_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying, 'RETRYING'::character varying, 'CANCELLED'::character varying])::text[]))),
    CONSTRAINT tasks_task_type_check CHECK (((task_type)::text = ANY ((ARRAY['PARSE_DOCUMENT'::character varying, 'GENERATE_EMBEDDINGS'::character varying, 'GENERATE_NOTES'::character varying, 'GENERATE_FLASHCARDS'::character varying, 'GENERATE_QUIZ'::character varying, 'GRADE_QUIZ_ATTEMPT'::character varying, 'ANSWER_CONVERSATION_TURN'::character varying, 'RESUME_AGENT_RUN'::character varying, 'MAINTAIN_CONVERSATION_MEMORY'::character varying, 'ASK_DOCUMENT'::character varying, 'EXPORT_MARKDOWN'::character varying])::text[])))
);


--
-- Name: topic_learning_memory; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.topic_learning_memory (
    workspace_id uuid NOT NULL,
    scope_id uuid NOT NULL,
    topic_key character varying(512) NOT NULL,
    topic text NOT NULL,
    mastery double precision DEFAULT 0.5 NOT NULL,
    confidence double precision DEFAULT 0 NOT NULL,
    evidence_weight double precision DEFAULT 0 NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    correct_count integer DEFAULT 0 NOT NULL,
    incorrect_count integer DEFAULT 0 NOT NULL,
    hint_count integer DEFAULT 0 NOT NULL,
    total_response_time_ms bigint DEFAULT 0 NOT NULL,
    consecutive_correct integer DEFAULT 0 NOT NULL,
    consecutive_incorrect integer DEFAULT 0 NOT NULL,
    recent_trend double precision DEFAULT 0 NOT NULL,
    last_activity_at timestamp with time zone,
    last_reviewed_at timestamp with time zone,
    next_review_at timestamp with time zone,
    needs_review boolean DEFAULT false NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    lapse_count integer DEFAULT 0 NOT NULL,
    stability_days double precision DEFAULT 1 NOT NULL,
    calibration_error double precision DEFAULT 0 NOT NULL,
    easy_attempts integer DEFAULT 0 NOT NULL,
    medium_attempts integer DEFAULT 0 NOT NULL,
    hard_attempts integer DEFAULT 0 NOT NULL,
    response_time_count integer DEFAULT 0 NOT NULL
);


--
-- Name: user_ai_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_ai_settings (
    user_id uuid NOT NULL,
    embedding_provider character varying(255),
    gemini_api_key character varying(512),
    gemini_embedding_model character varying(255),
    gemini_llm_model character varying(255),
    llm_provider character varying(255),
    openai_api_key character varying(512),
    openai_embedding_model character varying(255),
    openai_llm_model character varying(255),
    updated_at timestamp(6) with time zone
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    created_at timestamp(6) with time zone,
    display_name character varying(255),
    email character varying(255),
    updated_at timestamp(6) with time zone
);


--
-- Name: agent_run_snapshots agent_run_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_snapshots
    ADD CONSTRAINT agent_run_snapshots_pkey PRIMARY KEY (message_id);


--
-- Name: agent_run_steps agent_run_steps_message_id_step_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_steps
    ADD CONSTRAINT agent_run_steps_message_id_step_index_key UNIQUE (message_id, step_index);


--
-- Name: agent_run_steps agent_run_steps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_steps
    ADD CONSTRAINT agent_run_steps_pkey PRIMARY KEY (id);


--
-- Name: agent_task_waits agent_task_waits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_task_waits
    ADD CONSTRAINT agent_task_waits_pkey PRIMARY KEY (task_id, message_id);


--
-- Name: conversation_task_targets conversation_task_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_task_targets
    ADD CONSTRAINT conversation_task_targets_pkey PRIMARY KEY (task_id);


--
-- Name: document_ai_note_sections document_ai_note_sections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_ai_note_sections
    ADD CONSTRAINT document_ai_note_sections_pkey PRIMARY KEY (id);


--
-- Name: document_ai_notes document_ai_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_ai_notes
    ADD CONSTRAINT document_ai_notes_pkey PRIMARY KEY (id);


--
-- Name: document_chunks document_chunks_document_id_chunk_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_document_id_chunk_index_key UNIQUE (document_id, chunk_index);


--
-- Name: document_chunks document_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_pkey PRIMARY KEY (id);


--
-- Name: document_editable_notes document_editable_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_editable_notes
    ADD CONSTRAINT document_editable_notes_pkey PRIMARY KEY (id);


--
-- Name: document_embeddings document_embeddings_document_id_source_table_source_id_prov_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_embeddings
    ADD CONSTRAINT document_embeddings_document_id_source_table_source_id_prov_key UNIQUE (document_id, source_table, source_id, provider, model);


--
-- Name: document_embeddings document_embeddings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_embeddings
    ADD CONSTRAINT document_embeddings_pkey PRIMARY KEY (id);


--
-- Name: document_layout_blocks document_layout_blocks_document_id_page_number_block_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_layout_blocks
    ADD CONSTRAINT document_layout_blocks_document_id_page_number_block_index_key UNIQUE (document_id, page_number, block_index);


--
-- Name: document_layout_blocks document_layout_blocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_layout_blocks
    ADD CONSTRAINT document_layout_blocks_pkey PRIMARY KEY (id);


--
-- Name: document_markdown_documents document_markdown_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_markdown_documents
    ADD CONSTRAINT document_markdown_documents_pkey PRIMARY KEY (id);


--
-- Name: document_markdown_pages document_markdown_pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_markdown_pages
    ADD CONSTRAINT document_markdown_pages_pkey PRIMARY KEY (id);


--
-- Name: document_page_assets document_page_assets_document_id_page_number_asset_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_page_assets
    ADD CONSTRAINT document_page_assets_document_id_page_number_asset_type_key UNIQUE (document_id, page_number, asset_type);


--
-- Name: document_page_assets document_page_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_page_assets
    ADD CONSTRAINT document_page_assets_pkey PRIMARY KEY (id);


--
-- Name: document_parse_manifests document_parse_manifests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parse_manifests
    ADD CONSTRAINT document_parse_manifests_pkey PRIMARY KEY (document_id);


--
-- Name: document_parse_results document_parse_results_document_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parse_results
    ADD CONSTRAINT document_parse_results_document_id_key UNIQUE (document_id);


--
-- Name: document_parse_results document_parse_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_parse_results
    ADD CONSTRAINT document_parse_results_pkey PRIMARY KEY (id);


--
-- Name: document_visual_regions document_visual_regions_document_id_page_number_region_inde_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_visual_regions
    ADD CONSTRAINT document_visual_regions_document_id_page_number_region_inde_key UNIQUE (document_id, page_number, region_index);


--
-- Name: document_visual_regions document_visual_regions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_visual_regions
    ADD CONSTRAINT document_visual_regions_pkey PRIMARY KEY (id);


--
-- Name: document_vlm_results document_vlm_results_document_id_page_number_region_index_p_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_vlm_results
    ADD CONSTRAINT document_vlm_results_document_id_page_number_region_index_p_key UNIQUE (document_id, page_number, region_index, provider, model);


--
-- Name: document_vlm_results document_vlm_results_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_vlm_results
    ADD CONSTRAINT document_vlm_results_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: flashcard_decks flashcard_decks_document_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_decks
    ADD CONSTRAINT flashcard_decks_document_id_version_key UNIQUE (document_id, version);


--
-- Name: flashcard_decks flashcard_decks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_decks
    ADD CONSTRAINT flashcard_decks_pkey PRIMARY KEY (id);


--
-- Name: flashcard_review_states flashcard_review_states_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_review_states
    ADD CONSTRAINT flashcard_review_states_pkey PRIMARY KEY (user_id, flashcard_id);


--
-- Name: flashcards flashcards_deck_id_dedupe_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcards
    ADD CONSTRAINT flashcards_deck_id_dedupe_hash_key UNIQUE (deck_id, dedupe_hash);


--
-- Name: flashcards flashcards_deck_id_source_group_index_item_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcards
    ADD CONSTRAINT flashcards_deck_id_source_group_index_item_index_key UNIQUE (deck_id, source_group_index, item_index);


--
-- Name: flashcards flashcards_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcards
    ADD CONSTRAINT flashcards_pkey PRIMARY KEY (id);


--
-- Name: folders folders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.folders
    ADD CONSTRAINT folders_pkey PRIMARY KEY (id);


--
-- Name: learning_artifact_links learning_artifact_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_artifact_links
    ADD CONSTRAINT learning_artifact_links_pkey PRIMARY KEY (workspace_id, topic_key, artifact_type, artifact_id);


--
-- Name: learning_events learning_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_events
    ADD CONSTRAINT learning_events_pkey PRIMARY KEY (id);


--
-- Name: learning_events learning_events_workspace_id_external_event_id_topic_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_events
    ADD CONSTRAINT learning_events_workspace_id_external_event_id_topic_key_key UNIQUE (workspace_id, external_event_id, topic_key);


--
-- Name: learning_goals learning_goals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_goals
    ADD CONSTRAINT learning_goals_pkey PRIMARY KEY (id);


--
-- Name: learning_memory_corrections learning_memory_corrections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_memory_corrections
    ADD CONSTRAINT learning_memory_corrections_pkey PRIMARY KEY (id);


--
-- Name: learning_memory_history learning_memory_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_memory_history
    ADD CONSTRAINT learning_memory_history_pkey PRIMARY KEY (id);


--
-- Name: learning_preferences learning_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_preferences
    ADD CONSTRAINT learning_preferences_pkey PRIMARY KEY (workspace_id, preference_key);


--
-- Name: learning_strategy_experiments learning_strategy_experiments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_strategy_experiments
    ADD CONSTRAINT learning_strategy_experiments_pkey PRIMARY KEY (workspace_id, experiment_key);


--
-- Name: learning_study_plans learning_study_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_study_plans
    ADD CONSTRAINT learning_study_plans_pkey PRIMARY KEY (id);


--
-- Name: learning_topic_edges learning_topic_edges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.learning_topic_edges
    ADD CONSTRAINT learning_topic_edges_pkey PRIMARY KEY (workspace_id, from_topic_key, to_topic_key, relation);


--
-- Name: mistake_memory mistake_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mistake_memory
    ADD CONSTRAINT mistake_memory_pkey PRIMARY KEY (workspace_id, scope_id, topic_key, mistake_fingerprint);


--
-- Name: notes notes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notes
    ADD CONSTRAINT notes_pkey PRIMARY KEY (id);


--
-- Name: quiz_answers quiz_answers_attempt_id_question_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_answers
    ADD CONSTRAINT quiz_answers_attempt_id_question_id_key UNIQUE (attempt_id, question_id);


--
-- Name: quiz_answers quiz_answers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_answers
    ADD CONSTRAINT quiz_answers_pkey PRIMARY KEY (id);


--
-- Name: quiz_attempts quiz_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_attempts
    ADD CONSTRAINT quiz_attempts_pkey PRIMARY KEY (id);


--
-- Name: quiz_questions quiz_questions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_questions
    ADD CONSTRAINT quiz_questions_pkey PRIMARY KEY (id);


--
-- Name: quiz_questions quiz_questions_quiz_set_id_dedupe_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_questions
    ADD CONSTRAINT quiz_questions_quiz_set_id_dedupe_hash_key UNIQUE (quiz_set_id, dedupe_hash);


--
-- Name: quiz_questions quiz_questions_quiz_set_id_source_group_index_item_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_questions
    ADD CONSTRAINT quiz_questions_quiz_set_id_source_group_index_item_index_key UNIQUE (quiz_set_id, source_group_index, item_index);


--
-- Name: quiz_sets quiz_sets_document_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_sets
    ADD CONSTRAINT quiz_sets_document_id_version_key UNIQUE (document_id, version);


--
-- Name: quiz_sets quiz_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_sets
    ADD CONSTRAINT quiz_sets_pkey PRIMARY KEY (id);


--
-- Name: rag_conversation_summaries rag_conversation_summaries_conversation_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_conversation_summaries
    ADD CONSTRAINT rag_conversation_summaries_conversation_id_version_key UNIQUE (conversation_id, version);


--
-- Name: rag_conversation_summaries rag_conversation_summaries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_conversation_summaries
    ADD CONSTRAINT rag_conversation_summaries_pkey PRIMARY KEY (id);


--
-- Name: rag_conversations rag_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_conversations
    ADD CONSTRAINT rag_conversations_pkey PRIMARY KEY (id);


--
-- Name: rag_memories rag_memories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_memories
    ADD CONSTRAINT rag_memories_pkey PRIMARY KEY (id);


--
-- Name: rag_message_citations rag_message_citations_message_id_citation_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_message_citations
    ADD CONSTRAINT rag_message_citations_message_id_citation_index_key UNIQUE (message_id, citation_index);


--
-- Name: rag_message_citations rag_message_citations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_message_citations
    ADD CONSTRAINT rag_message_citations_pkey PRIMARY KEY (id);


--
-- Name: rag_messages rag_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_messages
    ADD CONSTRAINT rag_messages_pkey PRIMARY KEY (id);


--
-- Name: rag_user_preferences rag_user_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_user_preferences
    ADD CONSTRAINT rag_user_preferences_pkey PRIMARY KEY (user_id, preference_key);


--
-- Name: study_execution_leases study_execution_leases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_execution_leases
    ADD CONSTRAINT study_execution_leases_pkey PRIMARY KEY (lease_key);


--
-- Name: study_generation_checkpoints study_generation_checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_generation_checkpoints
    ADD CONSTRAINT study_generation_checkpoints_pkey PRIMARY KEY (generation_type, set_id, source_group_index);


--
-- Name: study_task_targets study_task_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_task_targets
    ADD CONSTRAINT study_task_targets_pkey PRIMARY KEY (task_id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: topic_learning_memory topic_learning_memory_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.topic_learning_memory
    ADD CONSTRAINT topic_learning_memory_pkey PRIMARY KEY (workspace_id, scope_id, topic_key);


--
-- Name: document_ai_note_sections uq_document_ai_note_sections_note_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_ai_note_sections
    ADD CONSTRAINT uq_document_ai_note_sections_note_index UNIQUE (note_id, section_index);


--
-- Name: document_ai_notes uq_document_ai_notes_document_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_ai_notes
    ADD CONSTRAINT uq_document_ai_notes_document_version UNIQUE (document_id, note_version);


--
-- Name: document_editable_notes uq_document_editable_notes_document; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_editable_notes
    ADD CONSTRAINT uq_document_editable_notes_document UNIQUE (document_id);


--
-- Name: user_ai_settings user_ai_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_ai_settings
    ADD CONSTRAINT user_ai_settings_pkey PRIMARY KEY (user_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_agent_run_steps_message; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_run_steps_message ON public.agent_run_steps USING btree (message_id, step_index);


--
-- Name: idx_agent_task_waits_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_agent_task_waits_task ON public.agent_task_waits USING btree (task_id);


--
-- Name: idx_document_embeddings_document_domain; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_document_embeddings_document_domain ON public.document_embeddings USING btree (document_id, source_domain);


--
-- Name: idx_document_embeddings_embedding_hnsw_3072; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_document_embeddings_embedding_hnsw_3072 ON public.document_embeddings USING hnsw (((embedding)::public.halfvec(3072)) public.halfvec_cosine_ops) WHERE ((embedding IS NOT NULL) AND (embedding_dimension = 3072));


--
-- Name: idx_document_embeddings_exact_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_document_embeddings_exact_search ON public.document_embeddings USING gin (exact_search_text public.gin_trgm_ops);


--
-- Name: idx_document_embeddings_provider_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_document_embeddings_provider_model ON public.document_embeddings USING btree (embedding_provider, embedding_model);


--
-- Name: idx_document_embeddings_search_vector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_document_embeddings_search_vector ON public.document_embeddings USING gin (search_vector);


--
-- Name: idx_documents_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_user_created ON public.documents USING btree (user_id, created_at DESC);


--
-- Name: idx_flashcard_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flashcard_due ON public.flashcard_review_states USING btree (user_id, due_at) WHERE ((status)::text <> 'SUSPENDED'::text);


--
-- Name: idx_flashcards_deck; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_flashcards_deck ON public.flashcards USING btree (deck_id, source_group_index, item_index);


--
-- Name: idx_learning_artifacts_topic; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_artifacts_topic ON public.learning_artifact_links USING btree (workspace_id, topic_key, status, last_interacted_at DESC);


--
-- Name: idx_learning_edges_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_edges_from ON public.learning_topic_edges USING btree (workspace_id, from_topic_key, weight DESC);


--
-- Name: idx_learning_events_artifact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_events_artifact ON public.learning_events USING btree (workspace_id, artifact_type, artifact_id);


--
-- Name: idx_learning_events_workspace_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_events_workspace_time ON public.learning_events USING btree (workspace_id, occurred_at DESC);


--
-- Name: idx_learning_goals_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_goals_active ON public.learning_goals USING btree (workspace_id, status, deadline, priority DESC);


--
-- Name: idx_learning_history_topic; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_learning_history_topic ON public.learning_memory_history USING btree (workspace_id, topic_key, recorded_at DESC);


--
-- Name: idx_mistake_memory_rank; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mistake_memory_rank ON public.mistake_memory USING btree (workspace_id, occurrences DESC, last_seen_at DESC);


--
-- Name: idx_notes_source_document_kind; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notes_source_document_kind ON public.notes USING btree (source_document_id, source_kind, created_at);


--
-- Name: idx_notes_user_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_notes_user_updated ON public.notes USING btree (user_id, updated_at DESC);


--
-- Name: idx_quiz_answers_attempt; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quiz_answers_attempt ON public.quiz_answers USING btree (attempt_id, graded_by);


--
-- Name: idx_quiz_questions_set; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quiz_questions_set ON public.quiz_questions USING btree (quiz_set_id, source_group_index, item_index);


--
-- Name: idx_rag_citations_message; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rag_citations_message ON public.rag_message_citations USING btree (message_id, citation_index);


--
-- Name: idx_rag_conversations_user_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rag_conversations_user_recent ON public.rag_conversations USING btree (user_id, last_message_at DESC NULLS LAST);


--
-- Name: idx_rag_conversations_user_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rag_conversations_user_updated ON public.rag_conversations USING btree (user_id, updated_at DESC);


--
-- Name: idx_rag_memories_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rag_memories_user_status ON public.rag_memories USING btree (user_id, status);


--
-- Name: idx_rag_messages_conversation_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_rag_messages_conversation_created ON public.rag_messages USING btree (conversation_id, created_at, id);


--
-- Name: idx_tasks_document_type_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_document_type_created ON public.tasks USING btree (document_id, task_type, created_at DESC);


--
-- Name: idx_tasks_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_user_created ON public.tasks USING btree (user_id, created_at DESC);


--
-- Name: idx_tasks_user_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tasks_user_status_created ON public.tasks USING btree (user_id, status, created_at DESC);


--
-- Name: idx_topic_memory_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_topic_memory_due ON public.topic_learning_memory USING btree (workspace_id, next_review_at) WHERE needs_review;


--
-- Name: idx_topic_memory_weak; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_topic_memory_weak ON public.topic_learning_memory USING btree (workspace_id, needs_review, mastery, next_review_at);


--
-- Name: uq_document_embeddings_source_provider_model; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_document_embeddings_source_provider_model ON public.document_embeddings USING btree (source_domain, source_object_type, source_object_id, embedding_provider, embedding_model);


--
-- Name: uq_markdown_documents_document; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_markdown_documents_document ON public.document_markdown_documents USING btree (document_id);


--
-- Name: uq_markdown_pages_document_page; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_markdown_pages_document_page ON public.document_markdown_pages USING btree (document_id, page_number);


--
-- Name: flashcard_decks trg_flashcard_checkpoint_cleanup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_flashcard_checkpoint_cleanup AFTER DELETE ON public.flashcard_decks FOR EACH ROW EXECUTE FUNCTION public.cleanup_study_generation_checkpoints();


--
-- Name: quiz_sets trg_quiz_checkpoint_cleanup; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_quiz_checkpoint_cleanup AFTER DELETE ON public.quiz_sets FOR EACH ROW EXECUTE FUNCTION public.cleanup_study_generation_checkpoints();


--
-- Name: agent_run_snapshots agent_run_snapshots_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_snapshots
    ADD CONSTRAINT agent_run_snapshots_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.rag_messages(id) ON DELETE CASCADE;


--
-- Name: agent_run_steps agent_run_steps_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_run_steps
    ADD CONSTRAINT agent_run_steps_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.rag_messages(id) ON DELETE CASCADE;


--
-- Name: agent_task_waits agent_task_waits_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_task_waits
    ADD CONSTRAINT agent_task_waits_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.rag_messages(id) ON DELETE CASCADE;


--
-- Name: flashcard_decks fk_flashcard_decks_document; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_decks
    ADD CONSTRAINT fk_flashcard_decks_document FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE NOT VALID;


--
-- Name: flashcard_decks fk_flashcard_decks_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_decks
    ADD CONSTRAINT fk_flashcard_decks_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE NOT VALID;


--
-- Name: flashcard_review_states fk_flashcard_reviews_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_review_states
    ADD CONSTRAINT fk_flashcard_reviews_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE NOT VALID;


--
-- Name: quiz_attempts fk_quiz_attempts_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_attempts
    ADD CONSTRAINT fk_quiz_attempts_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE NOT VALID;


--
-- Name: quiz_sets fk_quiz_sets_document; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_sets
    ADD CONSTRAINT fk_quiz_sets_document FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE NOT VALID;


--
-- Name: quiz_sets fk_quiz_sets_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_sets
    ADD CONSTRAINT fk_quiz_sets_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE NOT VALID;


--
-- Name: flashcard_review_states flashcard_review_states_flashcard_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcard_review_states
    ADD CONSTRAINT flashcard_review_states_flashcard_id_fkey FOREIGN KEY (flashcard_id) REFERENCES public.flashcards(id) ON DELETE CASCADE;


--
-- Name: flashcards flashcards_deck_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.flashcards
    ADD CONSTRAINT flashcards_deck_id_fkey FOREIGN KEY (deck_id) REFERENCES public.flashcard_decks(id) ON DELETE CASCADE;


--
-- Name: quiz_answers quiz_answers_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_answers
    ADD CONSTRAINT quiz_answers_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.quiz_attempts(id) ON DELETE CASCADE;


--
-- Name: quiz_answers quiz_answers_question_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_answers
    ADD CONSTRAINT quiz_answers_question_id_fkey FOREIGN KEY (question_id) REFERENCES public.quiz_questions(id) ON DELETE CASCADE;


--
-- Name: quiz_attempts quiz_attempts_quiz_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_attempts
    ADD CONSTRAINT quiz_attempts_quiz_set_id_fkey FOREIGN KEY (quiz_set_id) REFERENCES public.quiz_sets(id) ON DELETE CASCADE;


--
-- Name: quiz_questions quiz_questions_quiz_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quiz_questions
    ADD CONSTRAINT quiz_questions_quiz_set_id_fkey FOREIGN KEY (quiz_set_id) REFERENCES public.quiz_sets(id) ON DELETE CASCADE;


--
-- Name: rag_message_citations rag_message_citations_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rag_message_citations
    ADD CONSTRAINT rag_message_citations_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.rag_messages(id) ON DELETE CASCADE;


--
-- Name: study_task_targets study_task_targets_attempt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.study_task_targets
    ADD CONSTRAINT study_task_targets_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES public.quiz_attempts(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
