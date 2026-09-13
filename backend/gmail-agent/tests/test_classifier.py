"""Offline unit tests for classifier.classify_threads."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # gmail-agent/

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gmail_classifier import classify_threads


def _fake_ai_client(parsed: dict):
    """Build a fake ai_client with an awaitable .models.generate_content."""
    response = SimpleNamespace(parsed=parsed, text=None)
    models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    return SimpleNamespace(models=models)


class ClassifyThreadsTests(unittest.IsolatedAsyncioTestCase):
    def _threads(self):
        return [
            {"thread_id": "t1", "subject": "Thank you for applying", "sender": "a@co.com", "snippet": "..."},
            {"thread_id": "t2", "subject": "Newsletter", "sender": "b@co.com", "snippet": "..."},
        ]

    async def test_keeps_only_completion_entries_and_merges_fields(self):
        threads = self._threads()
        parsed = {
            "results": [
                {
                    "thread_id": "t1",
                    "is_completion": True,
                    "company": "Acme",
                    "role": "SWE Intern",
                    "status": "application received",
                    "confidence": 0.9,
                },
                {
                    "thread_id": "t2",
                    "is_completion": False,
                },
            ]
        }
        ai_client = _fake_ai_client(parsed)

        result = await classify_threads(ai_client, threads, model="test-model")

        self.assertEqual(len(result), 1)
        entry = result[0]
        # Original thread fields preserved.
        self.assertEqual(entry["thread_id"], "t1")
        self.assertEqual(entry["subject"], "Thank you for applying")
        self.assertEqual(entry["sender"], "a@co.com")
        # Extracted fields merged on top.
        self.assertEqual(entry["company"], "Acme")
        self.assertEqual(entry["role"], "SWE Intern")
        self.assertEqual(entry["status"], "application received")
        self.assertEqual(entry["confidence"], 0.9)
        ai_client.models.generate_content.assert_awaited_once()

    async def test_drops_entries_below_min_confidence(self):
        threads = self._threads()
        parsed = {
            "results": [
                {
                    "thread_id": "t1",
                    "is_completion": True,
                    "company": "Acme",
                    "role": "SWE Intern",
                    "status": "received",
                    "confidence": 0.3,
                },
            ]
        }
        ai_client = _fake_ai_client(parsed)

        result = await classify_threads(
            ai_client, threads, model="test-model", min_confidence=0.5
        )

        self.assertEqual(result, [])

    async def test_empty_threads_short_circuits_without_api_call(self):
        ai_client = _fake_ai_client({"results": []})

        result = await classify_threads(ai_client, [], model="test-model")

        self.assertEqual(result, [])
        ai_client.models.generate_content.assert_not_awaited()
        self.assertEqual(ai_client.models.generate_content.await_count, 0)


if __name__ == "__main__":
    unittest.main()
