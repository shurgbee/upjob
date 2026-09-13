"""Offline unit tests for matcher.match_emails."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # gmail-agent/

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gmail_matcher import match_emails


def _fake_ai_client(parsed: dict):
    response = SimpleNamespace(parsed=parsed, text=None)
    models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    return SimpleNamespace(models=models)


def _candidates():
    return [
        {"index": 0, "ctid": "(0,1)", "company": "Acme", "role": "SWE", "metadata": {}},
        {"index": 1, "ctid": "(0,2)", "company": "Globex", "role": "PM", "metadata": {}},
    ]


class MatchEmailsTests(unittest.IsolatedAsyncioTestCase):
    def _emails(self):
        return [{"thread_id": "t1", "company": "Acme", "role": "SWE"}]

    async def test_valid_candidate_index_maps_to_ctid(self):
        emails = self._emails()
        parsed = {
            "matches": [
                {"thread_id": "t1", "candidate_index": 0, "confidence": 0.9},
            ]
        }
        ai_client = _fake_ai_client(parsed)

        result = await match_emails(ai_client, emails, _candidates(), model="test-model")

        self.assertEqual(result, {"t1": "(0,1)"})

    async def test_null_candidate_index_is_none(self):
        emails = self._emails()
        parsed = {"matches": [{"thread_id": "t1", "candidate_index": None, "confidence": 0.9}]}
        ai_client = _fake_ai_client(parsed)

        result = await match_emails(ai_client, emails, _candidates(), model="test-model")

        self.assertIsNone(result["t1"])

    async def test_out_of_range_index_is_none(self):
        emails = self._emails()
        parsed = {"matches": [{"thread_id": "t1", "candidate_index": 99, "confidence": 0.9}]}
        ai_client = _fake_ai_client(parsed)

        result = await match_emails(ai_client, emails, _candidates(), model="test-model")

        self.assertIsNone(result["t1"])

    async def test_low_confidence_is_none(self):
        emails = self._emails()
        parsed = {"matches": [{"thread_id": "t1", "candidate_index": 0, "confidence": 0.2}]}
        ai_client = _fake_ai_client(parsed)

        result = await match_emails(
            ai_client, emails, _candidates(), model="test-model", min_confidence=0.5
        )

        self.assertIsNone(result["t1"])

    async def test_empty_candidates_returns_all_none_without_api_call(self):
        emails = [
            {"thread_id": "t1", "company": "Acme", "role": "SWE"},
            {"thread_id": "t2", "company": "Globex", "role": "PM"},
        ]
        ai_client = _fake_ai_client({"matches": []})

        result = await match_emails(ai_client, emails, [], model="test-model")

        self.assertEqual(result, {"t1": None, "t2": None})
        ai_client.models.generate_content.assert_not_awaited()

    async def test_empty_emails_returns_empty_dict(self):
        ai_client = _fake_ai_client({"matches": []})

        result = await match_emails(ai_client, [], _candidates(), model="test-model")

        self.assertEqual(result, {})
        ai_client.models.generate_content.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
