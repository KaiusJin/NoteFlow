"""Regression tests for the 2026-08-30 reliability remediation.

Covers the P0 fixes: thread-local user AI settings (no cross-user key leaks),
poison-pill payloads dead-lettered instead of crashing the consumer, bounded
redelivery for unexpectedly failed tasks, and the LaTeX-safe JSON escape
repair.
"""

import json
import threading
import time
import unittest
from unittest.mock import patch

from noteflow_worker.config import clear_task_ai_overrides, ai_setting
from noteflow_worker.notes.providers import escape_invalid_json_backslashes
from noteflow_worker.queue.redis_queue import RedisTaskQueue, TaskPayload
from noteflow_worker.runtime.execution_context import task_execution_scope
from noteflow_worker.task_execution import TaskExecutionState


class UserAiSettingsThreadIsolationTest(unittest.TestCase):
    def tearDown(self):
        clear_task_ai_overrides()

    def test_overrides_are_thread_local(self):
        from noteflow_worker import user_settings
        from noteflow_worker.config import settings

        row = {
            "gemini_api_key": "user-a-key",
            "openai_api_key": "",
            "llm_provider": "gemini",
            "gemini_llm_model": "gemini-user-a",
            "openai_llm_model": "",
            "embedding_provider": "gemini",
            "gemini_embedding_model": "embed-a",
            "openai_embedding_model": "",
        }

        observed = {}

        def run_other_user():
            # A concurrent task on another pooled thread applies its own (empty)
            # settings; it must never observe user A's snapshot.
            user_settings.apply_user_ai_settings("user-b")
            observed["other_thread_key"] = ai_setting("gemini_api_key")

        with patch.object(settings, "gemini_api_key", "env-key"):
            with patch.object(user_settings, "_load_row", side_effect=lambda user_id: row if user_id == "user-a" else None):
                user_settings.apply_user_ai_settings("user-a")
                self.assertEqual(ai_setting("gemini_api_key"), "user-a-key")
                self.assertEqual(ai_setting("gemini_notes_model"), "gemini-user-a")
                self.assertEqual(ai_setting("embedding_provider"), "gemini")
                thread = threading.Thread(target=run_other_user)
                thread.start()
                thread.join()
                # The applying thread keeps its own snapshot untouched.
                self.assertEqual(ai_setting("gemini_api_key"), "user-a-key")

        # The other thread fell back to the environment value, not user A's key.
        self.assertEqual(observed["other_thread_key"], "env-key")
        clear_task_ai_overrides()
        with patch.object(settings, "gemini_api_key", "env-key"):
            self.assertEqual(ai_setting("gemini_api_key"), "env-key")

    def test_missing_row_clears_previous_snapshot(self):
        from noteflow_worker import user_settings
        from noteflow_worker.config import settings

        row = {
            "gemini_api_key": "user-a-key",
            "openai_api_key": "",
            "llm_provider": "",
            "gemini_llm_model": "",
            "openai_llm_model": "",
            "embedding_provider": "",
            "gemini_embedding_model": "",
            "openai_embedding_model": "",
        }
        with patch.object(settings, "gemini_api_key", "env-key"):
            with patch.object(user_settings, "_load_row", return_value=row):
                user_settings.apply_user_ai_settings("user-a")
                self.assertEqual(ai_setting("gemini_api_key"), "user-a-key")
            # A user with no settings row (or a transient DB error) must not
            # inherit the previous task's snapshot.
            with patch.object(user_settings, "_load_row", return_value=None):
                user_settings.apply_user_ai_settings("user-b")
                self.assertEqual(ai_setting("gemini_api_key"), "env-key")
            with patch.object(user_settings, "_load_row", side_effect=RuntimeError("no table")):
                user_settings.apply_user_ai_settings("user-c")
                self.assertEqual(ai_setting("gemini_api_key"), "env-key")


class PoisonPayloadTest(unittest.TestCase):
    class FakeRedis:
        def __init__(self):
            self.lists: dict[str, list] = {}
            self.hashes: dict[str, dict] = {}
            self.zsets: dict[str, dict] = {}

        def rpush(self, name, *values):
            self.lists.setdefault(name, []).extend(values)

        def lpop(self, name):
            values = self.lists.get(name)
            if values:
                return values.pop(0)
            return None

        def pipeline(self):
            return self

        def execute(self):
            return []

        def hset(self, name, key, value):
            self.hashes.setdefault(name, {})[key] = value

        def hdel(self, name, key):
            self.hashes.get(name, {}).pop(key, None)

        def zrem(self, name, key):
            self.zsets.get(name, {}).pop(key, None)

        def eval(self, script, keys_count, *args):
            if keys_count == 3:
                queue_name, payloads_key, deadlines_key, lease_id, deadline = args
                payload = self.lpop(queue_name)
                if payload is None:
                    return None
                self.hset(payloads_key, lease_id, payload)
                self.zadd(deadlines_key, {lease_id: float(deadline)})
                return payload
            raise AssertionError(f"unexpected eval keys_count={keys_count}")

        def zadd(self, name, values):
            self.zsets.setdefault(name, {}).update(values)

    def make_queue(self):
        fake = self.FakeRedis()
        with patch("noteflow_worker.queue.redis_queue.redis.Redis.from_url", return_value=fake):
            queue = RedisTaskQueue()
        return queue, fake

    def test_malformed_json_is_dead_lettered_not_raised(self):
        queue, fake = self.make_queue()
        queue._client.rpush(queue.queue_name(0), "this is not json")
        payload = queue.pop()
        self.assertIsNone(payload)
        dead = fake.lists.get(queue.dead_letter_key, [])
        self.assertEqual(len(dead), 1)
        envelope = json.loads(dead[0])
        self.assertIn("JSONDecodeError", envelope["reason"])
        self.assertEqual(envelope["payload"], "this is not json")
        # The lease was released, so the queue stays empty of stuck processing.
        self.assertFalse(fake.hashes.get(queue._lease_payloads_key))

    def test_payload_missing_required_fields_is_dead_lettered(self):
        queue, fake = self.make_queue()
        queue._client.rpush(queue.queue_name(0), json.dumps({"taskType": "PARSE_DOCUMENT"}))
        payload = queue.pop()
        self.assertIsNone(payload)
        self.assertEqual(len(fake.lists.get(queue.dead_letter_key, [])), 1)

    def test_delivery_attempt_survives_push_decode_round_trip(self):
        queue, _ = self.make_queue()
        payload = TaskPayload("t1", "doc", "user", "PARSE_DOCUMENT", delivery_attempt=2)
        queue.push(payload)
        raw = queue._client.lists[queue.queue_name(payload.resolved_priority)][0]
        decoded = queue.pop()
        self.assertEqual(decoded.delivery_attempt, 2)
        self.assertIn("deliveryAttempt", raw)


class RequeueOrDeadLetterTest(unittest.TestCase):
    def test_failed_task_is_requeued_with_incremented_attempt(self):
        from noteflow_worker import main as worker_main
        from noteflow_worker.config import settings

        payload = TaskPayload(
            "t1", "doc", "user", "PARSE_DOCUMENT", delivery_attempt=0, lease_id="lease-1"
        )
        requeued = []

        class FakeQueue:
            def push(self, value):
                requeued.append(value)

            def ack(self, value):
                requeued.append("acked")

            def dead_letter(self, value, reason):
                raise AssertionError("should not dead-letter on first failure")

        class FakeExecutions:
            def state(self, task_id):
                return TaskExecutionState("PROCESSING", 0, "lease-1")

            def retry_or_fail(self, value, reason, max_retries):
                return TaskExecutionState("RETRYING", 1, None)

        worker_main.requeue_or_dead_letter(FakeQueue(), FakeExecutions(), payload, "RuntimeError: boom")
        self.assertEqual(requeued[0].delivery_attempt, 1)
        self.assertEqual(requeued[0].task_id, "t1")
        self.assertEqual(requeued[1], "acked")

    def test_exhausted_task_goes_to_dead_letter(self):
        from noteflow_worker import main as worker_main
        from noteflow_worker.config import settings

        payload = TaskPayload(
            "t1", "doc", "user", "PARSE_DOCUMENT",
            delivery_attempt=settings.queue_max_redeliveries, lease_id="lease-1",
        )
        dead = []

        class FakeQueue:
            def push(self, value):
                raise AssertionError("should not requeue past the budget")

            def ack(self, value):
                raise AssertionError("ack is owned by dead_letter()")

            def dead_letter(self, value, reason):
                dead.append((value, reason))

        class FakeExecutions:
            def state(self, task_id):
                return TaskExecutionState("PROCESSING", settings.queue_max_redeliveries, "lease-1")

            def retry_or_fail(self, value, reason, max_retries):
                return TaskExecutionState("FAILED", settings.queue_max_redeliveries + 1, None)

        worker_main.requeue_or_dead_letter(FakeQueue(), FakeExecutions(), payload, "RuntimeError: boom")
        self.assertEqual(dead[0][0].task_id, "t1")
        self.assertIn("exhausted", dead[0][1])

    def test_old_delivery_is_acked_when_another_execution_owns_the_task(self):
        from noteflow_worker import main as worker_main

        payload = TaskPayload("t1", "doc", "user", "PARSE_DOCUMENT", lease_id="old-lease")
        actions = []

        class FakeQueue:
            def push(self, value):
                raise AssertionError("a stale delivery must not create more work")

            def ack(self, value):
                actions.append(("ack", value.lease_id))

            def dead_letter(self, value, reason):
                raise AssertionError("a task with a current owner is not dead-lettered")

        class FakeExecutions:
            def state(self, task_id):
                return TaskExecutionState("PROCESSING", 1, "old-lease")

            def retry_or_fail(self, value, reason, max_retries):
                # The CAS lost after the initial state read.
                return TaskExecutionState("PROCESSING", 1, "new-lease")

        worker_main.requeue_or_dead_letter(FakeQueue(), FakeExecutions(), payload, "timeout")
        self.assertEqual(actions, [("ack", "old-lease")])


class InternalApiAuthenticationTest(unittest.TestCase):
    def test_service_headers_include_current_workspace(self):
        from noteflow_worker.config import settings
        from noteflow_worker.internal_api import internal_api_headers

        with patch.object(settings, "noteflow_internal_token", "x" * 32):
            with task_execution_scope("lease", "workspace-123"):
                self.assertEqual(
                    internal_api_headers(),
                    {
                        "Content-Type": "application/json",
                        "X-NoteFlow-Internal-Token": "x" * 32,
                        "X-NoteFlow-Workspace-Id": "workspace-123",
                    },
                )

    def test_service_headers_reject_missing_workspace(self):
        from noteflow_worker.config import settings
        from noteflow_worker.internal_api import internal_api_headers

        with patch.object(settings, "noteflow_internal_token", "x" * 32):
            with self.assertRaisesRegex(RuntimeError, "workspace context"):
                internal_api_headers()


class JsonEscapeRepairTest(unittest.TestCase):
    # Inputs model raw model output: single backslashes inside a JSON string
    # (which is exactly why the plain json.loads attempt failed upstream).
    def test_latex_command_prefix_is_preserved_not_corrupted(self):
        raw = '{"markdown": "so $\\begin{aligned} x \\end{aligned}$"}'
        repaired = json.loads(escape_invalid_json_backslashes(raw))
        self.assertEqual(repaired["markdown"], "so $\\begin{aligned} x \\end{aligned}$")

    def test_theta_is_not_eaten_as_tab(self):
        raw = '{"markdown": "$\\theta$"}'
        repaired = json.loads(escape_invalid_json_backslashes(raw))
        self.assertEqual(repaired["markdown"], "$\\theta$")

    def test_genuinely_invalid_escapes_are_still_doubled(self):
        raw = '{"markdown": "bad \\q escape"}'
        repaired = json.loads(escape_invalid_json_backslashes(raw))
        self.assertEqual(repaired["markdown"], "bad \\q escape")


if __name__ == "__main__":
    unittest.main()
