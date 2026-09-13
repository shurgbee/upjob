"""Offline unit tests for subagents and token optimization mechanisms."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from subagents import (
    compute_cache_key,
    evaluate_repository_component,
    extract_python_structural_signatures,
    filter_file_tree,
    get_cached_evaluation,
    is_eligible_file,
    run_evaluator_subagent,
    run_scout_subagent,
    store_cached_evaluation,
)


class TestDeterministicFiltersAndAST(unittest.TestCase):
    """Test suite for Tier 0 deterministic filters and AST signature extraction."""

    def test_is_eligible_file(self):
        self.assertTrue(is_eligible_file("src/main.py"))
        self.assertTrue(is_eligible_file("pkg/proxy/proxy.go"))
        self.assertTrue(is_eligible_file("Dockerfile"))

        # Ineligible
        self.assertFalse(is_eligible_file("node_modules/express/index.js"))
        self.assertFalse(is_eligible_file(".git/config"))
        self.assertFalse(is_eligible_file(".venv/bin/activate"))
        self.assertFalse(is_eligible_file("tests/test_main.py"))
        self.assertFalse(is_eligible_file("poetry.lock"))
        self.assertFalse(is_eligible_file("assets/logo.png"))

    def test_filter_file_tree(self):
        paths = [
            "node_modules/foo.js",
            "src/core.py",
            "tests/test_core.py",
            "README.md",
            "src/engine.py",
        ]
        filtered = filter_file_tree(paths)
        self.assertEqual(filtered, ["README.md", "src/core.py", "src/engine.py"])

    def test_extract_python_structural_signatures(self):
        sample_code = """
class ReverseProxy:
    \"\"\"A reverse proxy server.\"\"\"

    def __init__(self, port, target):
        self.port = port

    async def handle_request(self, reader, writer):
        pass

def create_server():
    return None
"""
        signatures = extract_python_structural_signatures(sample_code)
        self.assertIn("class ReverseProxy:", signatures)
        self.assertIn("def __init__(self, port, target)", signatures)
        self.assertIn("async def handle_request(self, reader, writer)", signatures)
        self.assertIn("def create_server()", signatures)

    def test_caching_layer(self):
        key = compute_cache_key("commit-abc1234", "ETL Pipeline")
        self.assertIsNone(get_cached_evaluation(key))

        mock_result = {"score": 95, "passed": True}
        store_cached_evaluation(key, mock_result)
        self.assertEqual(get_cached_evaluation(key), mock_result)


class TestSubagentsExecution(unittest.IsolatedAsyncioTestCase):
    """Test suite for Scout and Evaluator subagents mocking LLM responses."""

    async def test_run_scout_subagent(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = {
            "candidate_files": ["src/pipeline/etl.py", "src/core.py"],
            "reasoning": "Identified ETL pipeline data flow.",
        }
        mock_client.models.generate_content = AsyncMock(return_value=mock_response)

        files = ["README.md", "src/pipeline/etl.py", "src/core.py", "src/utils.py"]
        selected = await run_scout_subagent(
            mock_client,
            file_tree=files,
            architectural_component="ETL Pipeline",
        )
        self.assertEqual(selected, ["src/pipeline/etl.py", "src/core.py"])

    async def test_run_evaluator_subagent_high_score(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = {
            "score": 95,
            "passed": True,
            "key_mechanisms_found": ["buffered channel", "async worker pool"],
            "critique": "Clean pipeline design with excellent backpressure.",
        }
        mock_client.models.generate_content = AsyncMock(return_value=mock_response)

        snippets = {"src/pipeline.py": "class Pipeline: ..."}
        result = await run_evaluator_subagent(
            mock_client,
            architectural_component="ETL Pipeline",
            extracted_code_snippets=snippets,
        )
        self.assertEqual(result["score"], 95)
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["key_mechanisms_found"]), 2)

    async def test_evaluate_repository_component_end_to_end(self):
        mock_client = MagicMock()
        # Mock scout response
        scout_resp = MagicMock()
        scout_resp.parsed = {"candidate_files": ["src/engine.py"], "reasoning": "ok"}
        # Mock evaluator response
        eval_resp = MagicMock()
        eval_resp.parsed = {
            "score": 92,
            "passed": True,
            "key_mechanisms_found": ["cgroups v2 controller"],
            "critique": "Solid container isolation.",
        }
        mock_client.models.generate_content = AsyncMock(side_effect=[scout_resp, eval_resp])

        repo = {
            "README.md": "# My Container Engine",
            "src/engine.py": "class ContainerEngine:\n    def start(self):\n        pass",
            "tests/test.py": "def test(): pass",
        }
        res = await evaluate_repository_component(
            repo,
            architectural_component="Distributed Container Engine",
            commit_sha="sha999",
            ai_client=mock_client,
        )
        self.assertEqual(res["score"], 92)
        self.assertTrue(res["passed"])
        self.assertFalse(res["cached"])

        # Second run with same commit SHA hits cache (0 model calls)
        res_cached = await evaluate_repository_component(
            repo,
            architectural_component="Distributed Container Engine",
            commit_sha="sha999",
            ai_client=mock_client,
        )
        self.assertTrue(res_cached["cached"])
        self.assertEqual(res_cached["score"], 92)


if __name__ == "__main__":
    unittest.main()
