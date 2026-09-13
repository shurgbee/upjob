"""Offline unit tests for gmail_agent.sync_recent_threads orchestration.

Fully offline: the lazy ``from google import genai`` import is satisfied by
injecting a fake ``google.genai`` module into sys.modules, and every
module-level collaborator (search, classify, match, DB helpers) is patched on
the ``gmail_agent`` module itself.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # gmail-agent/

import os
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import gmail_agent


def _install_fake_genai():
    """Inject a fake google.genai module; returns the fake Client class."""
    fake_google = sys.modules.get("google")
    if fake_google is None:
        fake_google = types.ModuleType("google")
        sys.modules["google"] = fake_google

    fake_genai = types.ModuleType("google.genai")

    class _FakeAioClient:
        pass

    class _FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.aio = _FakeAioClient()

    fake_genai.Client = _FakeClient
    sys.modules["google.genai"] = fake_genai
    fake_google.genai = fake_genai
    return fake_genai


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeConn:
    def __init__(self):
        self.closed = False

    def transaction(self):
        return _FakeTransaction()

    async def close(self):
        self.closed = True


ENV = {"GEMINI_API_KEY": "fake-key", "DATABASE_URL": "postgresql://fake/db"}


def _patch_common(**overrides):
    """Patch every module-level collaborator gmail_agent.sync_recent_threads uses.

    Returns the dict of mock objects keyed by name for assertions.
    """
    defaults = dict(
        search_recent_threads=AsyncMock(return_value=[]),
        classify_threads=AsyncMock(return_value=[]),
        match_emails=AsyncMock(return_value={}),
        connect=AsyncMock(return_value=_FakeConn()),
        ensure_schema=AsyncMock(return_value=None),
        fetch_open_candidates=AsyncMock(return_value=[]),
        thread_exists=AsyncMock(return_value=False),
        attach_thread_to_row=AsyncMock(return_value=True),
        insert_new_row=AsyncMock(return_value=None),
        resolve_default_user_id=AsyncMock(return_value="default-user"),
    )
    defaults.update(overrides)
    return defaults


class SyncRecentThreadsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_fake_genai()

    def _patchers(self, mocks):
        return [patch.object(gmail_agent, name, mock) for name, mock in mocks.items()]

    async def _run_with(self, mocks, **kwargs):
        patchers = self._patchers(mocks)
        for p in patchers:
            p.start()
        try:
            with patch.dict(os.environ, ENV, clear=False):
                return await gmail_agent.sync_recent_threads(**kwargs)
        finally:
            for p in patchers:
                p.stop()

    async def test_matched_email_attaches_to_existing_row(self):
        threads = [
            {"thread_id": "t1", "subject": "Thank you", "sender": "a@co.com", "snippet": ""},
            {"thread_id": "t2", "subject": "Newsletter", "sender": "b@co.com", "snippet": ""},
        ]
        qualifying = [
            {"thread_id": "t1", "subject": "Thank you", "sender": "a@co.com",
             "snippet": "", "company": "Acme", "role": "SWE", "status": "received"},
        ]
        mocks = _patch_common(
            search_recent_threads=AsyncMock(return_value=threads),
            classify_threads=AsyncMock(return_value=qualifying),
            fetch_open_candidates=AsyncMock(return_value=[{"index": 0, "ctid": "(0,1)"}]),
            match_emails=AsyncMock(return_value={"t1": "(0,1)"}),
            attach_thread_to_row=AsyncMock(return_value=True),
        )

        summary = await self._run_with(mocks)

        self.assertEqual(summary["scanned"], 2)
        self.assertEqual(summary["qualified"], 1)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["skipped_existing"], 0)
        self.assertEqual(summary["errors"], [])
        mocks["insert_new_row"].assert_not_awaited()

    async def test_thread_already_linked_is_skipped(self):
        qualifying = [
            {"thread_id": "t1", "subject": "s", "sender": "a", "snippet": "", "company": "Acme",
             "role": "SWE", "status": "received"},
        ]
        mocks = _patch_common(
            search_recent_threads=AsyncMock(return_value=[{"thread_id": "t1"}]),
            classify_threads=AsyncMock(return_value=qualifying),
            thread_exists=AsyncMock(return_value=True),
        )

        summary = await self._run_with(mocks)

        self.assertEqual(summary["skipped_existing"], 1)
        self.assertEqual(summary["matched"], 0)
        self.assertEqual(summary["created"], 0)
        mocks["insert_new_row"].assert_not_awaited()
        mocks["attach_thread_to_row"].assert_not_awaited()

    async def test_no_match_decision_inserts_new_row(self):
        qualifying = [
            {"thread_id": "t1", "subject": "s", "sender": "a", "snippet": "", "company": "Acme",
             "role": "SWE", "status": "received"},
        ]
        mocks = _patch_common(
            search_recent_threads=AsyncMock(return_value=[{"thread_id": "t1"}]),
            classify_threads=AsyncMock(return_value=qualifying),
            match_emails=AsyncMock(return_value={"t1": None}),
        )

        summary = await self._run_with(mocks)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["matched"], 0)
        mocks["insert_new_row"].assert_awaited_once()
        mocks["attach_thread_to_row"].assert_not_awaited()

    async def test_attach_returns_false_falls_back_to_insert(self):
        qualifying = [
            {"thread_id": "t1", "subject": "s", "sender": "a", "snippet": "", "company": "Acme",
             "role": "SWE", "status": "received"},
        ]
        mocks = _patch_common(
            search_recent_threads=AsyncMock(return_value=[{"thread_id": "t1"}]),
            classify_threads=AsyncMock(return_value=qualifying),
            fetch_open_candidates=AsyncMock(return_value=[{"index": 0, "ctid": "(0,1)"}]),
            match_emails=AsyncMock(return_value={"t1": "(0,1)"}),
            attach_thread_to_row=AsyncMock(return_value=False),
        )

        summary = await self._run_with(mocks)

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["matched"], 0)
        mocks["insert_new_row"].assert_awaited_once()

    async def test_no_qualifying_threads_short_circuits_without_db(self):
        mocks = _patch_common(
            search_recent_threads=AsyncMock(return_value=[{"thread_id": "t1"}]),
            classify_threads=AsyncMock(return_value=[]),
        )

        summary = await self._run_with(mocks)

        self.assertEqual(summary["qualified"], 0)
        self.assertEqual(summary["scanned"], 1)
        mocks["connect"].assert_not_awaited()

    async def test_exception_processing_one_email_is_captured_not_raised(self):
        qualifying = [
            {"thread_id": "t1", "subject": "s", "sender": "a", "snippet": "", "company": "Acme",
             "role": "SWE", "status": "received"},
            {"thread_id": "t2", "subject": "s2", "sender": "b", "snippet": "", "company": "Globex",
             "role": "PM", "status": "received"},
        ]

        async def flaky_thread_exists(conn, thread_id):
            if thread_id == "t1":
                raise RuntimeError("boom")
            return False

        mocks = _patch_common(
            search_recent_threads=AsyncMock(return_value=[{"thread_id": "t1"}, {"thread_id": "t2"}]),
            classify_threads=AsyncMock(return_value=qualifying),
            thread_exists=AsyncMock(side_effect=flaky_thread_exists),
            match_emails=AsyncMock(return_value={"t1": None, "t2": None}),
        )

        summary = await self._run_with(mocks)

        self.assertEqual(len(summary["errors"]), 1)
        self.assertEqual(summary["errors"][0]["thread_id"], "t1")
        self.assertIn("boom", summary["errors"][0]["error"])
        # The second, non-flaky email still gets processed.
        self.assertEqual(summary["created"], 1)

    async def test_missing_gemini_api_key_raises(self):
        mocks = _patch_common()
        patchers = self._patchers(mocks)
        for p in patchers:
            p.start()
        try:
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://fake/db"}, clear=True):
                with self.assertRaises(RuntimeError):
                    await gmail_agent.sync_recent_threads()
        finally:
            for p in patchers:
                p.stop()


if __name__ == "__main__":
    unittest.main()
