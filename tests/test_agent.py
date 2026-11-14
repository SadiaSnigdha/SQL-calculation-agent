import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from build_database import build_database
from agent import RunResult, deterministic_spending_query, log_interaction, run_agent, sql_executor


class DatabaseAndSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db = Path(cls.temp_dir.name) / "sales.db"
        build_database(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_seeded_shape_and_big_table(self):
        connection = sqlite3.connect(self.db)
        try:
            counts = dict(connection.execute(
                "SELECT name, (SELECT COUNT(*) FROM sqlite_schema AS s2 WHERE s2.name=s1.name) FROM sqlite_schema s1 WHERE type='table'"
            ).fetchall())
            item_count = connection.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
        finally:
            connection.close()
        self.assertIn("customers", counts)
        self.assertGreaterEqual(item_count, 100)

    def test_select_returns_rows(self):
        result = sql_executor("SELECT COUNT(*) AS n FROM customers", self.db)
        self.assertEqual(result["rows"], [[30]])

    def test_write_and_multiple_statements_are_rejected(self):
        for query in ("DELETE FROM customers", "DROP TABLE customers", "SELECT 1; SELECT 2", "SELECT 1 -- comment"):
            result = sql_executor(query, self.db)
            self.assertFalse(result["ok"])
            self.assertTrue(any(term in result["error"].lower() for term in ("only", "allowed", "statement")))

    def test_row_limit(self):
        result = sql_executor("SELECT customer_id FROM customers", self.db, row_limit=5)
        self.assertEqual(len(result["rows"]), 5)

    def test_invalid_row_limit_is_rejected(self):
        for row_limit in (0, -1, True):
            result = sql_executor("SELECT customer_id FROM customers", self.db, row_limit=row_limit)
            self.assertFalse(result["ok"])
            self.assertIn("positive integer", result["error"])

    def test_interaction_log_appends_question_answer_and_timing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "conversation_log.jsonl"
            log_interaction(
                RunResult(
                    question="How many customers are there?",
                    answer="There are 30 customers.",
                    status="ok",
                    elapsed_ms=123,
                ),
                log_path,
            )

            record = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(record["question"], "How many customers are there?")
            self.assertEqual(record["answer"], "There are 30 customers.")
            self.assertEqual(record["status"], "ok")
            self.assertEqual(record["elapsed_ms"], 123)
            self.assertIn("timestamp", record)

    def test_customer_month_spending_query_returns_zero_when_no_orders_match(self):
        question = "How much did customer 1 spend in January 2025?"
        sql = deterministic_spending_query(question)
        self.assertIsNotNone(sql)
        result = sql_executor(sql, self.db)
        self.assertEqual(result["rows"], [[0.0]])

    def test_customer_month_spending_query_handles_december(self):
        sql = deterministic_spending_query("How much did customer 1 spend in December 2025?")
        self.assertIn("2025-12-01", sql)
        self.assertIn("2026-01-01", sql)

    def test_final_response_cannot_bypass_database_query(self):
        usage = {"prompt": 1, "completion": 1}
        with patch("agent.ollama_chat", side_effect=[
            ('{"final":"invented"}', usage),
            ('{"final":"grounded"}', usage),
        ]) as chat, patch("agent.sql_generation_tool", return_value=("SELECT 1", usage)), patch(
            "agent.sql_executor", return_value={"ok": True, "columns": ["value"], "rows": [[1]]}
        ):
            result = run_agent("What is the answer?", step_cap=3)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.answer, "grounded")
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(len(result.tool_calls), 1)

    def test_empty_model_decision_is_retried(self):
        usage = {"prompt": 1, "completion": 1}
        with patch("agent.ollama_chat", side_effect=[
            ('{"tool":"sql_generation","question":"What is the answer?"}', usage),
            ("{}", usage),
            ('{"final":"grounded"}', usage),
        ]), patch("agent.sql_generation_tool", return_value=("SELECT 1", usage)), patch(
            "agent.sql_executor", return_value={"ok": True, "columns": ["value"], "rows": [[1]]}
        ):
            result = run_agent("What is the answer?", step_cap=3)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.answer, "grounded")


if __name__ == "__main__":
    unittest.main()
