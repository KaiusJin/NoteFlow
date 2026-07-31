from pathlib import Path
import unittest


class SchemaGovernanceTest(unittest.TestCase):
    def test_worker_runtime_does_not_own_application_ddl(self):
        root = Path(__file__).parents[2] / "services" / "worker" / "noteflow_worker"
        forbidden = ("CREATE TABLE", "ALTER TABLE", "DROP CONSTRAINT", "CREATE EXTENSION")
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").upper()
            if any(statement in text for statement in forbidden):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual([], offenders)

    def test_java_runtime_schema_managers_were_removed(self):
        root = Path(__file__).parents[2] / "services" / "api" / "src" / "main" / "java"
        offenders = [
            path
            for path in root.rglob("*SchemaManager.java")
            if path.name != "RetrievalSchemaManager.java"
        ]
        self.assertEqual([], offenders)
