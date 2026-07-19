import json
import unittest

from noteflow_worker.conversation.agent import ToolCallingAgent, ToolResult, ToolSpec, agent_structured_response_json, build_tool_registry
from noteflow_worker.conversation.retrieval import Evidence
from noteflow_worker.memory.models import SourceScope, WorkingContext


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
        self.assertEqual(
            set(registry),
            {"search_notes", "get_document_section", "list_documents", "compare_sources", "generate_quiz", "create_flashcards"},
        )
        self.assertEqual(
            {name for name, spec in registry.items() if spec.kind == "sync"},
            {"search_notes", "get_document_section", "list_documents", "compare_sources"},
        )
        self.assertEqual(
            {name for name, spec in registry.items() if spec.kind == "async"},
            {"generate_quiz", "create_flashcards"},
        )


if __name__ == "__main__":
    unittest.main()
