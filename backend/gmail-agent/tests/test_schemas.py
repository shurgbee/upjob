"""Offline unit tests for schemas.py prompt builders and JSON schemas."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # gmail-agent/

import json
import unittest

from gmail_schemas import (
    CLASSIFY_SCHEMA,
    MATCH_SCHEMA,
    build_classify_prompt,
    build_match_prompt,
)


class ClassifyPromptTests(unittest.TestCase):
    def test_prompt_contains_thread_json(self):
        threads = [
            {"thread_id": "t1", "subject": "Thanks", "sender": "a@co.com", "snippet": "hi"},
        ]
        prompt = build_classify_prompt(threads)
        self.assertIn('"thread_id": "t1"', prompt)
        self.assertIn('"subject": "Thanks"', prompt)
        self.assertIn('"sender": "a@co.com"', prompt)

    def test_prompt_handles_empty_threads(self):
        prompt = build_classify_prompt([])
        self.assertIn("[]", prompt)

    def test_schema_has_required_keys(self):
        self.assertIn("results", CLASSIFY_SCHEMA["properties"])
        item_schema = CLASSIFY_SCHEMA["properties"]["results"]["items"]
        for key in ("thread_id", "is_completion", "company", "role", "status", "confidence"):
            self.assertIn(key, item_schema["properties"])
        self.assertEqual(item_schema["required"], ["thread_id", "is_completion"])


class MatchPromptTests(unittest.TestCase):
    def test_prompt_contains_emails_and_candidates_json(self):
        emails = [{"thread_id": "t1", "company": "Acme", "role": "SWE", "subject": "s", "snippet": "sn"}]
        candidates = [{"index": 0, "company": "Acme", "role": "SWE", "metadata": {}}]

        prompt = build_match_prompt(emails, candidates)

        self.assertIn('"thread_id": "t1"', prompt)
        self.assertIn('"company": "Acme"', prompt)
        self.assertIn('"index": 0', prompt)
        self.assertIn("EMAILS (JSON):", prompt)
        self.assertIn("CANDIDATES (JSON):", prompt)

    def test_schema_has_required_keys(self):
        self.assertIn("matches", MATCH_SCHEMA["properties"])
        item_schema = MATCH_SCHEMA["properties"]["matches"]["items"]
        for key in ("thread_id", "candidate_index", "confidence"):
            self.assertIn(key, item_schema["properties"])
        self.assertEqual(item_schema["required"], ["thread_id", "candidate_index"])


if __name__ == "__main__":
    unittest.main()
