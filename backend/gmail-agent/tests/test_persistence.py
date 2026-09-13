"""Offline unit tests for persistence.py.

Pure helpers (_load_json, _first_str) are tested directly. The async DB
functions are tested against a fake asyncpg-like connection object, never the
real asyncpg package.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # gmail-agent/

import json
import unittest

from gmail_persistence import (
    _first_str,
    _load_json,
    attach_thread_to_row,
    fetch_open_candidates,
    insert_new_row,
    resolve_default_user_id,
    thread_exists,
)


class LoadJsonTests(unittest.TestCase):
    def test_dict_passthrough(self):
        self.assertEqual(_load_json({"a": 1}), {"a": 1})

    def test_json_string_parsed(self):
        self.assertEqual(_load_json(json.dumps({"a": 1})), {"a": 1})

    def test_non_dict_json_string_returns_empty(self):
        self.assertEqual(_load_json(json.dumps([1, 2, 3])), {})

    def test_invalid_json_string_returns_empty(self):
        self.assertEqual(_load_json("not json"), {})

    def test_none_returns_empty(self):
        self.assertEqual(_load_json(None), {})

    def test_empty_string_returns_empty(self):
        self.assertEqual(_load_json(""), {})


class FirstStrTests(unittest.TestCase):
    def test_key_precedence(self):
        metadata = {"employer": "Globex", "company": "Acme"}
        self.assertEqual(_first_str(metadata, ("company", "employer")), "Acme")

    def test_falls_back_to_second_key(self):
        metadata = {"employer": "Globex"}
        self.assertEqual(_first_str(metadata, ("company", "employer")), "Globex")

    def test_blank_string_skipped(self):
        metadata = {"company": "   ", "employer": "Globex"}
        self.assertEqual(_first_str(metadata, ("company", "employer")), "Globex")

    def test_no_match_returns_empty(self):
        self.assertEqual(_first_str({}, ("company", "employer")), "")

    def test_non_string_value_skipped(self):
        metadata = {"company": 123, "employer": "Globex"}
        self.assertEqual(_first_str(metadata, ("company", "employer")), "Globex")


class FakeConn:
    """Minimal async stand-in for an asyncpg connection."""

    def __init__(self, fetchrow_result=None, fetch_result=None, execute_result=""):
        self.fetchrow_result = fetchrow_result
        self.fetch_result = fetch_result if fetch_result is not None else []
        self.execute_result = execute_result
        self.executed = []
        self.fetchrow_calls = []
        self.fetch_calls = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self.fetch_result

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return self.execute_result


class FakeRow(dict):
    """dict subclass so both row["col"] and row.get(...) work like asyncpg.Record."""


class ThreadExistsTests(unittest.IsolatedAsyncioTestCase):
    async def test_true_when_row_found(self):
        conn = FakeConn(fetchrow_result=FakeRow({"?column?": 1}))
        self.assertTrue(await thread_exists(conn, "abc123"))

    async def test_false_when_no_row(self):
        conn = FakeConn(fetchrow_result=None)
        self.assertFalse(await thread_exists(conn, "abc123"))


class AttachThreadToRowTests(unittest.IsolatedAsyncioTestCase):
    async def test_false_on_update_zero(self):
        conn = FakeConn(execute_result="UPDATE 0")
        result = await attach_thread_to_row(conn, "(0,1)", "thread-1")
        self.assertFalse(result)

    async def test_true_on_update_one(self):
        conn = FakeConn(execute_result="UPDATE 1")
        result = await attach_thread_to_row(conn, "(0,1)", "thread-1")
        self.assertTrue(result)


class InsertNewRowTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_insert_with_expected_args(self):
        conn = FakeConn(execute_result="INSERT 0 1")
        await insert_new_row(conn, "user-1", {"company": "Acme"}, "thread-1")
        self.assertEqual(len(conn.executed), 1)
        _query, args = conn.executed[0]
        self.assertEqual(args[0], "user-1")
        self.assertEqual(json.loads(args[1]), {"company": "Acme"})
        self.assertEqual(args[2], "thread-1")


class ResolveDefaultUserIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_str_id_when_row_present(self):
        conn = FakeConn(fetchrow_result=FakeRow({"user_id": "u-1"}))
        result = await resolve_default_user_id(conn)
        self.assertEqual(result, "u-1")

    async def test_returns_none_when_no_row(self):
        conn = FakeConn(fetchrow_result=None)
        result = await resolve_default_user_id(conn)
        self.assertIsNone(result)


class FetchOpenCandidatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_index_ctid_company_role_from_metadata(self):
        rows = [
            FakeRow(
                {
                    "ctid": "(0,1)",
                    "user_id": "u-1",
                    "job_id": "j-1",
                    "metadata": json.dumps({"company": "Acme", "role": "SWE Intern"}),
                    "job_title": "Fallback Title",
                    "job_url": "https://acme.example",
                }
            ),
            FakeRow(
                {
                    "ctid": "(0,2)",
                    "user_id": "u-1",
                    "job_id": None,
                    "metadata": {},
                    "job_title": "Backend Engineer",
                    "job_url": None,
                }
            ),
        ]
        conn = FakeConn(fetch_result=rows)

        candidates = await fetch_open_candidates(conn, "u-1")

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["index"], 0)
        self.assertEqual(candidates[0]["ctid"], "(0,1)")
        self.assertEqual(candidates[0]["company"], "Acme")
        self.assertEqual(candidates[0]["role"], "SWE Intern")

        # Second row: no metadata company/role, role falls back to job_title.
        self.assertEqual(candidates[1]["index"], 1)
        self.assertEqual(candidates[1]["company"], "")
        self.assertEqual(candidates[1]["role"], "Backend Engineer")

    async def test_scopes_query_by_user_id(self):
        conn = FakeConn(fetch_result=[])
        await fetch_open_candidates(conn, "u-1")
        _query, args = conn.fetch_calls[0]
        self.assertEqual(args, ("u-1",))

    async def test_no_user_id_omits_scoping_param(self):
        conn = FakeConn(fetch_result=[])
        await fetch_open_candidates(conn, None)
        _query, args = conn.fetch_calls[0]
        self.assertEqual(args, ())


if __name__ == "__main__":
    unittest.main()
