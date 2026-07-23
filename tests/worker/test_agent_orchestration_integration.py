"""Opt-in PostgreSQL coverage for durable Agent pause/resume subscriptions."""

import json
import os
import unittest
from uuid import uuid4

from noteflow_worker.conversation.store import ConversationStore


@unittest.skipUnless(os.getenv("NOTEFLOW_RUN_DB_TESTS") == "1", "requires local PostgreSQL")
class AgentContinuationIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.store = ConversationStore()
        self.store.ensure_conversation_schema()
        self.user_id, self.conversation_id = str(uuid4()), str(uuid4())
        self.message_id, self.artifact_task_id = str(uuid4()), str(uuid4())
        with self.store.connect() as conn:
            conn.execute("INSERT INTO rag_conversations(id,user_id,title) VALUES (%s,%s,'Agent continuation test')",
                         (self.conversation_id, self.user_id))
            conn.execute("""INSERT INTO rag_messages(id,conversation_id,role,status,content_markdown,token_count)
              VALUES (%s,%s,'ASSISTANT','GENERATING','',0)""", (self.message_id, self.conversation_id))
            conn.execute("""INSERT INTO tasks(id,document_id,user_id,task_type,status,current_step,progress,retry_count,priority)
              VALUES (%s,NULL,%s,'GENERATE_NOTES','COMPLETED','COMPLETED',100,0,1)""",
                         (self.artifact_task_id, self.user_id))

    def tearDown(self):
        with self.store.connect() as conn:
            conn.execute("DELETE FROM conversation_task_targets WHERE conversation_id=%s", (self.conversation_id,))
            conn.execute("DELETE FROM tasks WHERE user_id=%s", (self.user_id,))
            conn.execute("DELETE FROM rag_messages WHERE conversation_id=%s", (self.conversation_id,))
            conn.execute("DELETE FROM rag_conversations WHERE id=%s", (self.conversation_id,))

    def test_terminal_artifact_task_creates_exactly_one_resume_task(self):
        snapshot = {"phase": "WAITING", "paused": True, "waitingTaskId": self.artifact_task_id}
        self.store.pause_agent_run(self.message_id, self.conversation_id, self.user_id, "Generate notes",
                                   json.dumps(snapshot), self.artifact_task_id)
        created = self.store.create_resume_tasks(self.artifact_task_id)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["message_id"], self.message_id)
        self.assertEqual(self.store.create_resume_tasks(self.artifact_task_id), [])
        saved = self.store.load_agent_snapshot(self.message_id)
        self.assertEqual(saved["status"], "QUEUED")
        with self.store.connect() as conn:
            task = conn.execute("SELECT task_type,status FROM tasks WHERE id=%s", (created[0]["task_id"],)).fetchone()
        self.assertEqual((task["task_type"], task["status"]), ("RESUME_AGENT_RUN", "PENDING"))


if __name__ == "__main__":
    unittest.main()
