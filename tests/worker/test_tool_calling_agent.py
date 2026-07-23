import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from noteflow_worker.conversation.agent import (
    ToolCallingAgent,
    ToolResult,
    ToolSpec,
    agent_structured_response_json,
    agent_state_snapshot,
    build_tool_registry,
    enforce_orchestration_policy,
    validate_tool_arguments,
)
from noteflow_worker.conversation.retrieval import Evidence
from noteflow_worker.conversation.reflection import ArtifactEvaluation
from noteflow_worker.memory.models import SourceScope, WorkingContext
from noteflow_worker.study.generation_client import flashcard_request, quiz_request


def evidence(index=0):
    return Evidence(
        index=index,
        source_domain="PDF",
        source_object_type="DOCUMENT_CHUNK",
        source_object_id=f"chunk-{index}",
        document_id="00000000-0000-0000-0000-000000000001",
        document_title="Lecture",
        title="Section",
        page_start=1,
        page_end=1,
        text="Geometric distributions model trials until the first success.",
        snippet="Geometric distributions model trials...",
        similarity=0.9,
    )


def context():
    return WorkingContext(
        conversation_id="conversation-1",
        summary_text=None,
        summary_json=None,
        window=[],
        recalled_memories=[],
        window_token_count=0,
        summary_token_count=0,
        memory_token_count=0,
        source_scope=SourceScope(),
    )


class FakeStore:
    pass


class FakeQueue:
    def __init__(self):
        self.pushed = []

    def push(self, payload):
        self.pushed.append(payload)


class DisabledEmbeddings:
    provider_name = "disabled"
    model = ""


def wire_tool(thought, tool, args):
    """Flat wire-format decision the LLM emits for a tool call."""
    return {
        "thought": thought,
        "actionType": "tool",
        "tool": tool,
        "argsJson": json.dumps(args),
        "answerMarkdown": "",
        "citations": [],
        "confidence": 0,
        "insufficientEvidence": False,
    }


def wire_final(thought, answer, citations, confidence, insufficient=False):
    """Flat wire-format decision the LLM emits for a final answer."""
    return {
        "thought": thought,
        "actionType": "final_answer",
        "tool": "none",
        "argsJson": "{}",
        "answerMarkdown": answer,
        "citations": citations,
        "confidence": confidence,
        "insufficientEvidence": insufficient,
    }


class ScriptedLlm:
    provider = "test"
    model = "scripted"

    def __init__(self, decisions):
        self.decisions = list(decisions)

    def generate(self, prompt, schema, schema_name, validate):
        if schema_name == "noteflow_conversation_answer":
            parsed = {
                "answerMarkdown": "Fallback answer. [1]",
                "citations": [{"evidenceIndex": 0}],
                "confidence": 0.6,
                "insufficientEvidence": False,
            }
        else:
            parsed = self.decisions.pop(0)
        validate(parsed)
        return parsed


class ToolCallingAgentTest(unittest.TestCase):
    def agent(self, llm, tool_result, **kwargs):
        agent = ToolCallingAgent(FakeStore(), FakeQueue(), llm, DisabledEmbeddings(), **kwargs)
        agent.tools = {
            "search_notes": ToolSpec(
                "search_notes",
                "fake search",
                {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}},
                lambda _args, _state: tool_result,
                "sync",
            )
        }
        return agent

    def test_tool_then_final_answer_records_trace_and_evidence(self):
        checkpoints = []
        llm = ScriptedLlm([
            wire_tool("Need evidence.", "search_notes", {"query": "geometric"}),
            wire_final("Enough evidence.", "It models the first success. [1]", [{"evidenceIndex": 0}], 0.8),
        ])
        state = self.agent(llm, ToolResult(True, "found one", [evidence(0)])).run(
            "conversation-1",
            "user-1",
            "What is it?",
            context(),
            checkpoint_callback=lambda agent_state: checkpoints.append(len(agent_state.scratchpad)),
        )
        self.assertFalse(state.fallback_used)
        self.assertEqual(state.final.answer_markdown, "It models the first success. [1]")
        self.assertEqual(len(state.scratchpad), 2)
        self.assertEqual(checkpoints, [1, 2])
        payload = json.loads(agent_structured_response_json(state))
        self.assertTrue(payload["agent"]["enabled"])
        self.assertEqual(payload["agent"]["trace"][0]["tool"], "search_notes")
        self.assertNotIn("thought", payload["agent"]["trace"][0])

    def test_max_steps_triggers_direct_rag_fallback(self):
        llm = ScriptedLlm([
            wire_tool("Search again.", "search_notes", {"query": "same"}),
        ])
        state = self.agent(llm, ToolResult(True, "found one", [evidence(0)]), max_steps=1).run(
            "conversation-1", "user-1", "What is it?", context()
        )
        self.assertTrue(state.fallback_used)
        self.assertEqual(state.stop_reason, "max_steps")
        self.assertEqual(state.final.answer_markdown, "Fallback answer. [1]")
        self.assertEqual(state.scratchpad[-1].action_type, "fallback")

    def test_repeated_identical_tool_call_is_blocked_and_traced(self):
        llm = ScriptedLlm([
            wire_tool("Search.", "search_notes", {"query": "same"}),
            wire_tool("Search duplicate.", "search_notes", {"query": "same"}),
            wire_final("Answer insufficient.", "I need more evidence.", [], 0.2, insufficient=True),
        ])
        state = self.agent(llm, ToolResult(True, "found one", [evidence(0)]), max_steps=3).run(
            "conversation-1", "user-1", "What is it?", context()
        )
        self.assertFalse(state.scratchpad[1].ok)
        self.assertEqual(state.scratchpad[1].error, "repeated_tool_call")

    def test_registry_contains_full_sync_async_tool_set(self):
        registry = build_tool_registry()
        categories = {
            "retrieval": {"search_sources", "search_notes", "search_quiz_history", "search_flashcards",
                          "retrieve_related_chunks", "retrieve_previous_conversation"},
            "learning": {"generate_quiz", "generate_flashcards", "generate_ai_notes", "generate_summary",
                         "generate_study_guide", "generate_examples", "generate_practice_questions",
                         "record_learning_feedback", "set_learning_goal", "set_learning_preference",
                         "link_learning_artifact"},
            "workspace": {"read_markdown", "edit_markdown", "insert_section", "delete_section",
                          "rewrite_paragraph", "update_note", "save_artifact"},
            "analytics": {"analyze_quiz_performance", "find_weak_topics", "estimate_mastery",
                          "recommend_review_order", "detect_frequently_wrong_concepts", "get_learning_profile",
                          "get_weak_topics", "get_due_reviews", "get_learning_goals", "get_learning_preferences",
                          "find_learning_artifacts", "get_topic_graph", "get_mastery_trend"},
            "planning": {"create_study_plan", "break_down_task", "prioritize_tasks", "decide_next_action",
                         "select_documents", "estimate_time", "build_dynamic_study_plan"},
            "validation": {"verify_citation", "check_coverage", "detect_hallucination",
                           "evaluate_generated_quiz", "retry_generation"},
            "custom": {"correct_learning_memory"},
        }
        expected = set().union(*categories.values())
        self.assertEqual(len(expected), 50)
        self.assertEqual(set(registry), expected)
        self.assertTrue(all(spec.kind in {"sync", "async"} for spec in registry.values()))
        self.assertTrue(all(spec.args_schema.get("type") == "OBJECT" for spec in registry.values()))
        for category, names in categories.items():
            self.assertEqual({name for name, spec in registry.items() if spec.category == category}, names)

    def test_tool_argument_schema_rejects_missing_unknown_and_wrong_type(self):
        schema = build_tool_registry()["generate_quiz"].args_schema
        with self.assertRaisesRegex(ValueError, "missing required"):
            validate_tool_arguments({}, schema)
        with self.assertRaisesRegex(ValueError, "unknown field"):
            validate_tool_arguments({"documentIds": ["doc-1"], "typo": 1}, schema)
        with self.assertRaisesRegex(ValueError, "must be an array"):
            validate_tool_arguments({"documentIds": "doc-1"}, schema)
        validate_tool_arguments({"documentIds": ["doc-1"], "medium": 3, "includeExplanations": True}, schema)

    def test_planning_and_validation_tools_execute_deterministically(self):
        registry = build_tool_registry()
        prioritized = registry["prioritize_tasks"].handler({"tasks": [
            {"title": "Review", "urgency": 5, "impact": 5, "effortMinutes": 20},
            {"title": "Optional", "urgency": 1, "impact": 1, "effortMinutes": 5},
        ]}, object())
        self.assertTrue(prioritized.ok)
        self.assertIn("priorityScore", prioritized.observation)
        coverage = registry["check_coverage"].handler(
            {"markdown": "# Review\nBayes theorem and covariance", "requiredTopics": ["Bayes", "covariance"]}, object()
        )
        self.assertTrue(coverage.ok)

    def test_retry_generation_preserves_stored_configuration(self):
        registry = build_tool_registry()
        stored = {
            "id": "quiz-1", "title": "Retry me", "status": "PARTIAL",
            "source_scope_json": json.dumps({"documentIds": ["doc-1"], "chunkIds": ["chunk-1"], "focus": "Bayes"}),
            "generation_options_json": json.dumps({
                "difficultyCounts": {"EASY": 1, "MEDIUM": 4, "HARD": 2},
                "questionTypes": ["SHORT_ANSWER"], "includeExplanations": False,
            }),
        }
        with patch("noteflow_worker.conversation.agent_toolkit._one", return_value=stored), \
             patch("noteflow_worker.conversation.agent_toolkit.StudyGenerationClient") as client_type:
            client_type.return_value.create_targeted_quiz.return_value = {"kind": "quiz", "quizSetId": "quiz-1"}
            result = registry["retry_generation"].handler(
                {"artifactType": "QUIZ", "artifactId": "quiz-1"}, SimpleNamespace(user_id="user-1")
            )
        self.assertTrue(result.ok)
        request = client_type.return_value.create_targeted_quiz.call_args.args[0]
        self.assertEqual((request["easy"], request["medium"], request["hard"]), (1, 4, 2))
        self.assertEqual(request["questionTypes"], ["SHORT_ANSWER"])
        self.assertFalse(request["includeExplanations"])

    def test_workspace_mutation_requires_read_in_same_run(self):
        state = SimpleNamespace(scratchpad=[])
        with self.assertRaisesRegex(PermissionError, "read_markdown"):
            enforce_orchestration_policy(state, "edit_markdown", {"noteId": "note-1"})
        state.scratchpad.append(SimpleNamespace(ok=True, tool="read_markdown", args={"noteId": "note-1"}))
        enforce_orchestration_policy(state, "edit_markdown", {"noteId": "note-1"})

    def test_async_tool_pauses_then_resumes_through_mandatory_evaluation(self):
        first_llm = ScriptedLlm([wire_tool("Create it.", "async_quiz", {"documentIds": ["doc-1"]})])
        first = ToolCallingAgent(FakeStore(), FakeQueue(), first_llm, DisabledEmbeddings(), max_steps=6)
        first.tools = {"async_quiz": ToolSpec(
            "async_quiz", "test", {"type": "OBJECT", "properties": {
                "documentIds": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["documentIds"]},
            lambda _args, _state: ToolResult(True, "started", handle={
                "kind": "quiz", "quizSetId": "quiz-1", "taskId": "task-1"}), "async", "learning")}
        paused = first.run("conversation-1", "user-1", "Create a quiz", context())
        self.assertTrue(paused.paused)
        self.assertEqual(paused.phase, "WAITING")

        resumed_llm = ScriptedLlm([wire_final("Quality passed.", "Created successfully.", [], 0.9, insufficient=True)])
        resumed = ToolCallingAgent(FakeStore(), FakeQueue(), resumed_llm, DisabledEmbeddings(), max_steps=6)
        resumed.tools = first.tools
        with patch("noteflow_worker.conversation.agent.evaluate_pending_artifact",
                   return_value=ArtifactEvaluation(True, False, {"passed": True})):
            completed = resumed.run("conversation-1", "user-1", "Create a quiz", context(),
                                    snapshot=agent_state_snapshot(paused))
        self.assertFalse(completed.paused)
        self.assertEqual(completed.evaluation_count, 1)
        self.assertEqual(completed.scratchpad[-2].action_type, "evaluation")
        self.assertIsNotNone(completed.final)

    def test_failed_evaluation_automatically_retries_and_pauses_again(self):
        agent = ToolCallingAgent(FakeStore(), FakeQueue(), ScriptedLlm([]), DisabledEmbeddings(), max_steps=8)
        agent.tools = {
            "generate_quiz": ToolSpec("generate_quiz", "test", {
                "type": "OBJECT", "properties": {"documentIds": {"type": "ARRAY", "items": {"type": "STRING"}}},
                "required": ["documentIds"]}, lambda _args, _state: ToolResult(False, "unused"), "async", "learning"),
            "retry_generation": ToolSpec("retry_generation", "test", {
                "type": "OBJECT", "properties": {"artifactType": {"type": "STRING", "enum": ["QUIZ", "FLASHCARDS"]},
                                                     "artifactId": {"type": "STRING"}},
                "required": ["artifactType", "artifactId"]},
                lambda _args, _state: ToolResult(True, "retry started", handle={
                    "kind": "quiz", "quizSetId": "quiz-1", "taskId": "task-2"}), "async", "validation"),
        }
        snapshot = {
            "phase": "WAITING", "paused": True, "waitingTaskId": "task-1", "reflectionCount": 0,
            "pendingArtifact": {"tool": "generate_quiz", "args": {"documentIds": ["doc-1"]},
                                "rootTool": "generate_quiz", "rootArgs": {"documentIds": ["doc-1"]},
                                "handle": {"kind": "quiz", "quizSetId": "quiz-1", "taskId": "task-1"}},
        }
        failed = ArtifactEvaluation(False, True, {"status": "PARTIAL", "reason": "coverage"})
        with patch("noteflow_worker.conversation.agent.evaluate_pending_artifact", return_value=failed):
            retried = agent.run("conversation-1", "user-1", "Create a quiz", context(), snapshot=snapshot)
        self.assertTrue(retried.paused)
        self.assertEqual(retried.waiting_task_id, "task-2")
        self.assertEqual(retried.reflection_count, 1)
        self.assertEqual(retried.scratchpad[-1].action_type, "reflection")

    def test_agent_study_requests_map_context_to_shared_service_contract(self):
        quiz = quiz_request({
            "documentIds": ["doc-1"], "chunkIds": ["chunk-1"], "focus": "covariance",
            "easy": 1, "medium": 2, "hard": 0, "questionTypes": ["MULTIPLE_CHOICE"],
        })
        self.assertEqual(quiz["origin"], "AGENT")
        self.assertEqual(quiz["sourceChunkIds"], ["chunk-1"])
        self.assertEqual(quiz["questionTypes"], ["MULTIPLE_CHOICE"])
        cards = flashcard_request({"documentIds": ["doc-1"], "section": "Chapter 3", "count": 5})
        self.assertEqual(cards["section"], "Chapter 3")
        self.assertEqual(cards["count"], 5)


if __name__ == "__main__":
    unittest.main()
