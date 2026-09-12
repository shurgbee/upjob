"""Tests for retrieval.py — fully offline, no asyncpg or google-genai needed."""

from __future__ import annotations

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # resume-tailor (for schemas)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))  # backend (for common)

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from schemas import JobSpecification, SelectedProject
import retrieval


class FakeConn:
    """Mock asyncpg connection that returns preset rows and records calls."""

    def __init__(self, rows: list[dict]):
        """Initialize with a list of dicts to return from fetch()."""
        self.rows = rows
        self.last_call = None

    async def fetch(self, sql: str, *args):
        """Record the call and return preset rows (as dicts, not Records)."""
        self.last_call = (sql, args)
        return self.rows


class TestParseVector(unittest.TestCase):
    """Test parse_vector helper."""

    def test_normal_vector(self):
        """Parse a normal pgvector literal."""
        result = retrieval.parse_vector("[0.1,0.2,0.3]")
        self.assertEqual(result, [0.1, 0.2, 0.3])

    def test_negative_values(self):
        """Parse vector with negative values."""
        result = retrieval.parse_vector("[0.1,-0.2,-0.3]")
        self.assertEqual(result, [0.1, -0.2, -0.3])

    def test_empty_bracket(self):
        """Parse empty bracket notation."""
        result = retrieval.parse_vector("[]")
        self.assertEqual(result, [])

    def test_empty_string(self):
        """Parse empty string."""
        result = retrieval.parse_vector("")
        self.assertEqual(result, [])

    def test_with_spaces(self):
        """Parse with interior spaces."""
        result = retrieval.parse_vector("[ 0.1 , 0.2 , 0.3 ]")
        self.assertEqual(result, [0.1, 0.2, 0.3])

    def test_single_value(self):
        """Parse single value."""
        result = retrieval.parse_vector("[0.5]")
        self.assertEqual(result, [0.5])


class TestCosineSimilarity(unittest.TestCase):
    """Test cosine_similarity helper."""

    def test_identical_unit_vectors(self):
        """Identical unit vectors should have similarity ~1.0."""
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        result = retrieval.cosine_similarity(a, b)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_orthogonal_vectors(self):
        """Orthogonal unit vectors should have similarity ~0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = retrieval.cosine_similarity(a, b)
        self.assertAlmostEqual(result, 0.0, places=5)

    def test_opposite_vectors(self):
        """Opposite vectors should have similarity ~-1.0."""
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        result = retrieval.cosine_similarity(a, b)
        self.assertAlmostEqual(result, -1.0, places=5)

    def test_mismatched_length(self):
        """Vectors of different lengths should return 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0]
        result = retrieval.cosine_similarity(a, b)
        self.assertEqual(result, 0.0)

    def test_empty_vectors(self):
        """Empty vectors should return 0.0."""
        result = retrieval.cosine_similarity([], [])
        self.assertEqual(result, 0.0)

    def test_partial_match(self):
        """Partial overlap should be between 0 and 1."""
        # Two normalized vectors with some overlap
        a = [0.707, 0.707, 0.0]
        b = [0.707, 0.0, 0.707]
        result = retrieval.cosine_similarity(a, b)
        self.assertGreater(result, 0.0)
        self.assertLess(result, 1.0)


class TestSelectCandidates(unittest.TestCase):
    """Test select_candidates helper."""

    def test_keep_rows_above_threshold(self):
        """Keep rows with overlap_ratio >= threshold."""
        rows = [
            {"id": 1, "overlap_ratio": 0.9},
            {"id": 2, "overlap_ratio": 0.75},
            {"id": 3, "overlap_ratio": 0.5},
        ]
        result = retrieval.select_candidates(
            rows,
            threshold=0.8,
            min_candidates=1,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)

    def test_fallback_to_all_when_too_few(self):
        """Fall back to all rows if fewer than min_candidates qualify."""
        rows = [
            {"id": 1, "overlap_ratio": 0.9},
            {"id": 2, "overlap_ratio": 0.75},
            {"id": 3, "overlap_ratio": 0.5},
        ]
        result = retrieval.select_candidates(
            rows,
            threshold=0.8,
            min_candidates=3,
        )
        # Only 1 qualifies, but we need 3, so fallback to all
        self.assertEqual(len(result), 3)

    def test_none_overlap_ratio_excluded(self):
        """Rows with None overlap_ratio are excluded from threshold check."""
        rows = [
            {"id": 1, "overlap_ratio": 0.9},
            {"id": 2, "overlap_ratio": None},
            {"id": 3, "overlap_ratio": 0.85},
        ]
        result = retrieval.select_candidates(
            rows,
            threshold=0.8,
            min_candidates=1,
        )
        # Both 0.9 and 0.85 qualify
        self.assertEqual(len(result), 2)
        self.assertEqual([r["id"] for r in result], [1, 3])

    def test_empty_rows(self):
        """Empty rows list returns empty list."""
        result = retrieval.select_candidates(
            [],
            threshold=0.8,
            min_candidates=3,
        )
        self.assertEqual(result, [])

    def test_exact_threshold(self):
        """Rows exactly at threshold are included."""
        rows = [
            {"id": 1, "overlap_ratio": 0.8},
            {"id": 2, "overlap_ratio": 0.79},
        ]
        result = retrieval.select_candidates(
            rows,
            threshold=0.8,
            min_candidates=1,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)


class TestRankBySimilarity(unittest.TestCase):
    """Test rank_by_similarity helper."""

    def test_orders_by_similarity_descending(self):
        """Results are sorted by similarity descending."""
        rows = [
            {
                "id": 1,
                "name": "Project 1",
                "embedding_text": "[0.707, 0.707, 0.0]",
                "technologies": ["Python"],
                "architectures": ["Microservices"],
                "details_markdown": "Details 1",
                "overlap_ratio": 0.8,
            },
            {
                "id": 2,
                "name": "Project 2",
                "embedding_text": "[1.0, 0.0, 0.0]",
                "technologies": ["Java"],
                "architectures": ["Monolith"],
                "details_markdown": "Details 2",
                "overlap_ratio": 0.7,
            },
        ]
        job_vector = [1.0, 0.0, 0.0]
        result = retrieval.rank_by_similarity(rows, job_vector, top_k=2)

        # Project 2's vector [1.0, 0.0, 0.0] matches job_vector exactly (similarity ~1.0)
        # Project 1's vector [0.707, 0.707, 0.0] has similarity ~0.707
        self.assertEqual(result[0].project_id, 2)
        self.assertEqual(result[1].project_id, 1)

    def test_truncates_to_top_k(self):
        """Results are truncated to top_k."""
        rows = [
            {
                "id": i,
                "name": f"Project {i}",
                "embedding_text": "[1.0, 0.0]",
                "technologies": [],
                "architectures": [],
                "details_markdown": "",
                "overlap_ratio": 1.0 - (i * 0.1),
            }
            for i in range(1, 6)
        ]
        job_vector = [1.0, 0.0]
        result = retrieval.rank_by_similarity(rows, job_vector, top_k=3)

        self.assertEqual(len(result), 3)

    def test_builds_selected_project(self):
        """Each result is a SelectedProject with correct fields."""
        rows = [
            {
                "id": 42,
                "name": "Test Project",
                "embedding_text": "[1.0, 0.0, 0.0]",
                "technologies": ["Python", "Go"],
                "architectures": ["REST API"],
                "details_markdown": "Some details",
                "overlap_ratio": 0.75,
            },
        ]
        job_vector = [1.0, 0.0, 0.0]
        result = retrieval.rank_by_similarity(rows, job_vector, top_k=1)

        self.assertEqual(len(result), 1)
        project = result[0]
        self.assertIsInstance(project, SelectedProject)
        self.assertEqual(project.project_id, 42)
        self.assertEqual(project.name, "Test Project")
        self.assertEqual(project.technologies, ["Python", "Go"])
        self.assertEqual(project.architectures, ["REST API"])
        self.assertEqual(project.details_markdown, "Some details")
        self.assertAlmostEqual(project.overlap_ratio, 0.75, places=5)
        self.assertAlmostEqual(project.similarity, 1.0, places=5)

    def test_missing_embedding_text(self):
        """Missing embedding_text defaults to empty vector (similarity 0)."""
        rows = [
            {
                "id": 1,
                "name": "Project 1",
                "embedding_text": "",
                "technologies": [],
                "architectures": [],
                "details_markdown": "",
                "overlap_ratio": 0.5,
            },
        ]
        job_vector = [1.0, 0.0]
        result = retrieval.rank_by_similarity(rows, job_vector, top_k=1)

        self.assertEqual(result[0].similarity, 0.0)

    def test_missing_optional_fields(self):
        """Missing optional fields default to empty/zero."""
        rows = [
            {
                "id": 1,
                "name": "Project 1",
                "embedding_text": "[1.0]",
            },
        ]
        job_vector = [1.0]
        result = retrieval.rank_by_similarity(rows, job_vector, top_k=1)

        project = result[0]
        self.assertEqual(project.technologies, [])
        self.assertEqual(project.architectures, [])
        self.assertEqual(project.details_markdown, "")
        self.assertEqual(project.overlap_ratio, 0.0)


class TestFetchOverlapCandidates(unittest.IsolatedAsyncioTestCase):
    """Test fetch_overlap_candidates async function."""

    async def test_executes_sql_with_correct_args(self):
        """SQL is executed with technologies, user_id, and limit."""
        rows = [
            {
                "id": 1,
                "name": "Project 1",
                "technologies": ["Python"],
                "architectures": ["API"],
                "details_markdown": "Details",
                "embedding_text": "[0.5]",
                "overlap_ratio": 0.8,
            },
        ]
        conn = FakeConn(rows)

        result = await retrieval.fetch_overlap_candidates(
            conn,
            ["Python", "Go"],
            "user123",
            limit=5,
        )

        # Check that fetch was called with correct args
        self.assertIsNotNone(conn.last_call)
        sql, args = conn.last_call
        self.assertEqual(sql, retrieval.OVERLAP_SQL)
        self.assertEqual(args[0], ["Python", "Go"])
        self.assertEqual(args[1], "user123")
        self.assertEqual(args[2], 5)

    async def test_returns_dicts(self):
        """Results are returned as plain dicts."""
        rows = [
            {
                "id": 1,
                "name": "Project 1",
                "technologies": ["Python"],
                "architectures": ["API"],
                "details_markdown": "Details",
                "embedding_text": "[0.5]",
                "overlap_ratio": 0.8,
            },
        ]
        conn = FakeConn(rows)

        result = await retrieval.fetch_overlap_candidates(
            conn,
            ["Python"],
            None,
        )

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]["id"], 1)
        self.assertEqual(result[0]["name"], "Project 1")

    async def test_default_limit(self):
        """Default limit is used when not specified."""
        conn = FakeConn([])

        await retrieval.fetch_overlap_candidates(
            conn,
            [],
            None,
        )

        _, args = conn.last_call
        self.assertEqual(args[2], retrieval.DEFAULT_FILTER_LIMIT)


class TestSelectProjects(unittest.IsolatedAsyncioTestCase):
    """Test select_projects orchestrator."""

    async def test_end_to_end_with_mocked_embed(self):
        """Full pipeline with mocked embedding."""
        job = JobSpecification(
            title="Senior Backend Engineer",
            technologies=["Python", "Go"],
            architecture=["Microservices", "REST API"],
        )

        rows = [
            {
                "id": 1,
                "name": "Project A",
                "technologies": ["Python", "Go", "Rust"],
                "architectures": ["Microservices"],
                "details_markdown": "Details A",
                "embedding_text": "[1.0, 0.0, 0.0]",
                "overlap_ratio": 0.9,
            },
            {
                "id": 2,
                "name": "Project B",
                "technologies": ["Java"],
                "architectures": ["Monolith"],
                "details_markdown": "Details B",
                "embedding_text": "[0.0, 1.0, 0.0]",
                "overlap_ratio": 0.1,
            },
        ]
        conn = FakeConn(rows)

        # Mock the embedding function to return a fixed vector
        async def mock_embed(job_spec, *, api_key, model=None):
            return [1.0, 0.0, 0.0]  # Will give Project A highest similarity

        with patch("retrieval.embed_job_architecture", new=mock_embed):
            result = await retrieval.select_projects(
                conn,
                job,
                "user456",
                api_key="fake-key",
                top_k=2,
            )

        self.assertEqual(len(result), 2)
        # Project A should rank first due to higher similarity
        self.assertEqual(result[0].project_id, 1)
        self.assertEqual(result[0].name, "Project A")

    async def test_respects_top_k(self):
        """Respects the top_k limit."""
        job = JobSpecification(
            title="Test",
            technologies=["Python"],
            architecture=["API"],
        )

        rows = [
            {
                "id": i,
                "name": f"Project {i}",
                "technologies": ["Python"],
                "architectures": ["API"],
                "details_markdown": f"Details {i}",
                "embedding_text": "[1.0]",
                "overlap_ratio": 0.8,
            }
            for i in range(1, 6)
        ]
        conn = FakeConn(rows)

        async def mock_embed(job_spec, *, api_key, model=None):
            return [1.0]

        with patch("retrieval.embed_job_architecture", new=mock_embed):
            result = await retrieval.select_projects(
                conn,
                job,
                None,
                api_key="fake-key",
                top_k=2,
            )

        self.assertEqual(len(result), 2)

    async def test_passes_model_to_embed(self):
        """Model parameter is passed through to embed_job_architecture."""
        job = JobSpecification(
            title="Test",
            technologies=["Python"],
            architecture=["API"],
        )

        conn = FakeConn([])

        # Track what parameters embed_job_architecture was called with
        call_args = {}

        async def mock_embed(job_spec, *, api_key, model=None):
            call_args["api_key"] = api_key
            call_args["model"] = model
            return [1.0]

        with patch("retrieval.embed_job_architecture", new=mock_embed):
            await retrieval.select_projects(
                conn,
                job,
                None,
                api_key="test-key",
                model="custom-model",
                top_k=1,
            )

        self.assertEqual(call_args["api_key"], "test-key")
        self.assertEqual(call_args["model"], "custom-model")


class TestEmbedJobArchitecture(unittest.IsolatedAsyncioTestCase):
    """Test embed_job_architecture async function."""

    async def test_raises_on_empty_architecture(self):
        """Raises ValueError if architecture is empty."""
        job = JobSpecification(title="Test", architecture=[])

        with self.assertRaises(ValueError) as ctx:
            await retrieval.embed_job_architecture(job, api_key="fake-key")

        self.assertIn("no architecture", str(ctx.exception))

    async def test_raises_on_blank_architecture(self):
        """Raises ValueError if architecture contains only blanks."""
        job = JobSpecification(title="Test", architecture=["  ", ""])

        with self.assertRaises(ValueError) as ctx:
            await retrieval.embed_job_architecture(job, api_key="fake-key")

        self.assertIn("no architecture", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
