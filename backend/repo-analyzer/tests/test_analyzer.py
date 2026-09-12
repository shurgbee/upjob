"""Tests for the Gemini analysis layer, with the SDK stubbed out.

``analyzer`` imports ``google.genai`` lazily inside :func:`analyze_files`, which
lets these tests install a fake module in ``sys.modules`` and exercise the real
single-pass / map-reduce branching, prompt assembly and metadata accounting
without a network call or an API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analyzer  # noqa: E402
from ingestion import RepoFile  # noqa: E402
from schemas import DETAILS_SECTION_ORDER  # noqa: E402


class _FakeModels:
    def __init__(self, recorder: list[dict], responses: list[dict]):
        self._recorder = recorder
        self._responses = responses

    async def generate_content(self, *, model, contents, config):
        self._recorder.append(
            {
                "model": model,
                "prompt": contents,
                "schema": config["response_json_schema"],
                "system_instruction": config["system_instruction"],
            }
        )
        index = min(len(self._recorder) - 1, len(self._responses) - 1)
        return types.SimpleNamespace(parsed=self._responses[index], text=None)


class _FakeAio:
    def __init__(self, recorder, responses):
        self.models = _FakeModels(recorder, responses)
        self.closed = False

    async def aclose(self):
        self.closed = True


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.aio = _FakeAio(_FakeClient.recorder, _FakeClient.responses)
        _FakeClient.instances.append(self)


@contextlib.contextmanager
def fake_genai(responses: list[dict]):
    """Install a fake ``google.genai`` and yield the list of recorded calls."""
    recorder: list[dict] = []
    _FakeClient.recorder = recorder
    _FakeClient.responses = responses
    _FakeClient.instances = []

    genai_module = types.ModuleType("google.genai")
    genai_module.Client = _FakeClient
    google_module = types.ModuleType("google")
    google_module.genai = genai_module

    saved = {k: sys.modules.get(k) for k in ("google", "google.genai")}
    sys.modules["google"] = google_module
    sys.modules["google.genai"] = genai_module
    try:
        yield recorder
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def _analysis(name="Demo", technologies=None, architectures=None) -> dict:
    details = {key: [] for key in DETAILS_SECTION_ORDER}
    details["Summary"] = "A demo project."
    return {
        "project_specification": {
            "name": name,
            "description": "Demo description.",
            "technologies": technologies or ["Python"],
            "architectures": architectures or ["CLI Tool"],
        },
        "details": details,
    }


def run(coro):
    return asyncio.run(coro)


class UserContextBlockTests(unittest.TestCase):
    def test_absent_context_yields_empty_string(self):
        for value in (None, {}, "not a dict", 42, []):
            self.assertEqual(analyzer.build_user_context_block(value), "")

    def test_scalars_lists_and_dicts_are_rendered(self):
        block = analyzer.build_user_context_block(
            {
                "metrics_achieved": ["1M rows/day", "p99 40ms"],
                "project_impact": "Replaced   a cron job",
                "coverage": {"unit_tests": "92%"},
            }
        )
        self.assertIn("metrics achieved:", block)
        self.assertIn("  - 1M rows/day", block)
        # Internal whitespace runs are collapsed so a line cannot break layout.
        self.assertIn("project impact: Replaced a cron job", block)
        self.assertIn("  - unit tests: 92%", block)
        self.assertTrue(block.startswith("<user_supplied_context>"))

    def test_blank_values_are_dropped_entirely(self):
        self.assertEqual(
            analyzer.build_user_context_block({"metrics": [], "impact": "   "}), ""
        )


class TimelineBlockTests(unittest.TestCase):
    def test_no_dates_yields_empty_string(self):
        self.assertEqual(analyzer._timeline_block(None, None), "")

    def test_partial_dates_are_labelled_unknown(self):
        block = analyzer._timeline_block(None, "2025-01-01T00:00:00Z")
        self.assertIn("earliest commit: unknown", block)
        self.assertIn("latest commit: 2025-01-01T00:00:00Z", block)


class SelectHeaderFilesTests(unittest.TestCase):
    def test_only_priority_files_are_selected(self):
        files = [
            RepoFile("README.md", "readme"),
            RepoFile("src/main.py", "code"),
            RepoFile("package.json", "{}"),
        ]
        selected = [f.path for f in analyzer.select_header_files(files)]
        self.assertEqual(selected, ["README.md", "package.json"])

    def test_selection_is_capped(self):
        files = [
            RepoFile(name, "x")
            for name in (
                "README.md",
                "package.json",
                "requirements.txt",
                "pyproject.toml",
                "go.mod",
                "Cargo.toml",
            )
        ]
        selected = analyzer.select_header_files(files)
        self.assertEqual(len(selected), analyzer.MAX_HEADER_PRIORITY_FILES)


class AnalyzeFilesTests(unittest.TestCase):
    def test_empty_file_list_is_rejected(self):
        with self.assertRaises(ValueError):
            run(analyzer.analyze_files([], owner="o", repo="r", api_key="k"))

    def test_single_pass_when_everything_fits(self):
        files = [RepoFile("README.md", "A demo."), RepoFile("src/main.py", "print(1)")]
        with fake_genai([_analysis()]) as calls:
            result = run(
                analyzer.analyze_files(files, owner="o", repo="r", api_key="k")
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["system_instruction"], analyzer.SINGLE_PASS_INSTRUCTION)
        self.assertEqual(result["ingestion"]["mode"], "single_pass")
        self.assertEqual(result["ingestion"]["model_calls"], 1)
        self.assertEqual(result["ingestion"]["omitted_paths"], [])
        self.assertFalse(result["ingestion"]["truncated"])
        self.assertEqual(result["project_specification"]["name"], "Demo")

    def test_api_key_reaches_the_client(self):
        files = [RepoFile("README.md", "A demo.")]
        with fake_genai([_analysis()]):
            run(analyzer.analyze_files(files, owner="o", repo="r", api_key="secret"))
        self.assertEqual(_FakeClient.instances[0].api_key, "secret")
        self.assertTrue(_FakeClient.instances[0].aio.closed)

    def test_user_context_and_timeline_reach_the_prompt(self):
        files = [RepoFile("README.md", "A demo.")]
        with fake_genai([_analysis()]) as calls:
            run(
                analyzer.analyze_files(
                    files,
                    owner="o",
                    repo="r",
                    api_key="k",
                    start_time="2024-01-01T00:00:00Z",
                    end_time="2025-01-01T00:00:00Z",
                    user_context={"metrics": ["1M rows/day"]},
                )
            )
        prompt = calls[0]["prompt"]
        self.assertIn("1M rows/day", prompt)
        self.assertIn("earliest commit: 2024-01-01T00:00:00Z", prompt)

    def _big_files(self) -> list[RepoFile]:
        # Three module directories, each too large to share a single call.
        return [
            RepoFile("README.md", "readme"),
            RepoFile("alpha/a.py", "a" * 400),
            RepoFile("beta/b.py", "b" * 400),
            RepoFile("gamma/c.py", "c" * 400),
        ]

    def test_map_reduce_when_the_budget_is_exceeded(self):
        fragments = [
            {"technologies": ["Python"], "architectures": ["CLI Tool"], "Actions": ["Wrote alpha"]},
            {"technologies": ["python"], "architectures": ["Job Queue"], "Actions": ["Wrote beta"]},
        ]
        responses = fragments + [_analysis(technologies=["Python"], architectures=["CLI Tool", "Job Queue"])]
        with fake_genai(responses) as calls:
            result = run(
                analyzer.analyze_files(
                    self._big_files(),
                    owner="o",
                    repo="r",
                    api_key="k",
                    char_budget=700,
                )
            )

        self.assertEqual(result["ingestion"]["mode"], "map_reduce")
        chunks = result["ingestion"]["chunks"]
        self.assertGreater(chunks, 1)
        # One call per chunk, plus exactly one reduce call.
        self.assertEqual(len(calls), chunks + 1)
        self.assertEqual(result["ingestion"]["model_calls"], chunks + 1)
        self.assertFalse(result["ingestion"]["truncated"])
        self.assertEqual(result["ingestion"]["omitted_paths"], [])

        self.assertEqual(calls[0]["system_instruction"], analyzer.FRAGMENT_INSTRUCTION)
        self.assertEqual(calls[-1]["system_instruction"], analyzer.REDUCE_INSTRUCTION)

        # The reduce call sees merged findings, not raw source.
        reduce_prompt = calls[-1]["prompt"]
        self.assertIn("<merged_findings>", reduce_prompt)
        self.assertNotIn("a" * 400, reduce_prompt)

    def test_every_chunk_receives_the_global_header(self):
        with fake_genai([{"technologies": [], "architectures": []}] * 8) as calls:
            run(
                analyzer.analyze_files(
                    self._big_files(),
                    owner="acme",
                    repo="widget",
                    api_key="k",
                    char_budget=700,
                )
            )
        for call in calls[:-1]:
            self.assertIn("# Repository: acme/widget", call["prompt"])
            self.assertIn("README.md", call["prompt"])

    def test_chunk_count_is_capped_and_reported_as_truncated(self):
        files = [RepoFile("README.md", "readme")] + [
            RepoFile(f"mod{i:03d}/file.py", "x" * 400) for i in range(40)
        ]
        with fake_genai([{"technologies": [], "architectures": []}] * 64) as calls:
            result = run(
                analyzer.analyze_files(
                    files, owner="o", repo="r", api_key="k", char_budget=700
                )
            )
        self.assertEqual(result["ingestion"]["chunks"], analyzer.MAX_CHUNKS)
        self.assertEqual(len(calls), analyzer.MAX_CHUNKS + 1)
        self.assertTrue(result["ingestion"]["truncated"])
        self.assertTrue(result["ingestion"]["omitted_paths"])

    def test_concurrency_is_bounded(self):
        files = self._big_files()
        original = analyzer._generate_json
        live = 0
        peak = 0

        async def counting(*args, **kwargs):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0)
            try:
                return {"technologies": [], "architectures": []}
            finally:
                live -= 1

        analyzer._generate_json = counting
        try:
            with fake_genai([{"technologies": [], "architectures": []}]):
                run(
                    analyzer.analyze_files(
                        files,
                        owner="o",
                        repo="r",
                        api_key="k",
                        char_budget=700,
                        max_concurrency=1,
                    )
                )
        finally:
            analyzer._generate_json = original
        self.assertEqual(peak, 1)


class GenerateJsonTests(unittest.TestCase):
    def test_falls_back_to_text_when_parsed_is_absent(self):
        class Models:
            async def generate_content(self, **kwargs):
                return types.SimpleNamespace(parsed=None, text='{"ok": true}')

        client = types.SimpleNamespace(models=Models())
        result = run(
            analyzer._generate_json(
                client, model="m", prompt="p", schema={}, system_instruction="s"
            )
        )
        self.assertEqual(result, {"ok": True})

    def test_empty_response_raises(self):
        class Models:
            async def generate_content(self, **kwargs):
                return types.SimpleNamespace(parsed=None, text="")

        client = types.SimpleNamespace(models=Models())
        with self.assertRaises(RuntimeError):
            run(
                analyzer._generate_json(
                    client, model="m", prompt="p", schema={}, system_instruction="s"
                )
            )

    def test_non_object_json_raises(self):
        class Models:
            async def generate_content(self, **kwargs):
                return types.SimpleNamespace(parsed=None, text="[1, 2]")

        client = types.SimpleNamespace(models=Models())
        with self.assertRaises(RuntimeError):
            run(
                analyzer._generate_json(
                    client, model="m", prompt="p", schema={}, system_instruction="s"
                )
            )


if __name__ == "__main__":
    unittest.main()
