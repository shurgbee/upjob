"""Offline unit tests for resume_tailor.py.

All tests are run without database, Gemini API, or network access.
Dependencies (retrieval, generation, db.connect) are monkeypatched.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Set up paths for imports (tests/ is one level below resume-tailor/)
test_dir = pathlib.Path(__file__).resolve().parent
resume_tailor_dir = test_dir.parent
backend_dir = resume_tailor_dir.parent

sys.path.insert(0, str(resume_tailor_dir))  # resume-tailor
sys.path.insert(0, str(backend_dir))         # backend

import resume_tailor
from schemas import SelectedProject, JobSpecification


class TestTailorResume(unittest.TestCase):
    """Test the tailor_resume async function with mocked dependencies."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a minimal job specification for testing
        self.minimal_job_spec = {
            "Title": "Test Job",
            "Technologies": ["Python", "PostgreSQL"],
            "Architecture": ["ETL Pipeline"],
        }

        # Create a sample SelectedProject for testing
        self.sample_project = SelectedProject(
            project_id=1,
            name="Test Project",
            technologies=["Python", "PostgreSQL"],
            architectures=["ETL Pipeline"],
            details_markdown="# Test Project\n\nA sample project for testing.",
            overlap_ratio=0.8,
            similarity=0.9,
        )

    def _make_fake_conn(self):
        """Create a fake asyncpg connection for testing."""
        conn = AsyncMock()
        conn.close = AsyncMock()
        return conn

    def _make_async_select_projects(self, projects=None):
        """Create a mock async select_projects function."""
        if projects is None:
            projects = [self.sample_project]

        async def mock_select_projects(conn, job, user_id, **kwargs):
            return projects

        return mock_select_projects

    def _make_async_generate_all(self):
        """Create a mock async generate_all function that sets bullets."""
        async def mock_generate_all(job, projects, **kwargs):
            # Set sample bullets for each project
            for project in projects:
                project.bullets = [
                    "Accomplished X as measured by Y by doing Z.",
                    "Built A as measured by B through C.",
                    "Developed D as measured by E via F.",
                ]
            return projects

        return mock_generate_all

    def test_happy_path_writes_tex_file(self):
        """Test successful resume tailoring: retrieves, generates, writes file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = pathlib.Path(tmpdir) / "test_resume.tex"

            with patch("resume_tailor.connect", new_callable=AsyncMock) as mock_connect, \
                 patch("resume_tailor.retrieval.select_projects", new_callable=AsyncMock) as mock_select, \
                 patch("resume_tailor.generation.generate_all", new_callable=AsyncMock) as mock_generate:

                # Configure mocks
                fake_conn = self._make_fake_conn()
                mock_connect.return_value = fake_conn

                sample_project = SelectedProject(
                    project_id=1,
                    name="Sample Project",
                    technologies=["Python"],
                    architectures=["ETL"],
                    details_markdown="Sample markdown",
                    overlap_ratio=0.85,
                    similarity=0.9,
                )
                mock_select.return_value = [sample_project]

                async def mock_gen(job, projects, **kwargs):
                    for p in projects:
                        p.bullets = ["Bullet 1", "Bullet 2", "Bullet 3"]
                    return projects

                mock_generate.side_effect = mock_gen

                # Run the pipeline
                result = asyncio.run(
                    resume_tailor.tailor_resume(
                        self.minimal_job_spec,
                        user_id="test_user",
                        candidate_name="Test Candidate",
                        output_path=str(output_path),
                        gemini_api_key="test_key",
                        database_url="postgresql://test",
                    )
                )

                # Assert success
                self.assertEqual(result["status"], "ok")
                self.assertIsNone(result["error"])
                self.assertEqual(result["job_title"], "Test Job")
                self.assertEqual(result["candidate_name"], "Test Candidate")
                self.assertEqual(result["user_id"], "test_user")
                self.assertIsNotNone(result["output_path"])
                self.assertGreater(len(result["selected_projects"]), 0)
                self.assertGreater(result["tex_chars"], 0)

                # Assert file was written
                self.assertTrue(output_path.exists())
                content = output_path.read_text(encoding="utf-8")
                self.assertIn("Test Candidate", content)
                self.assertIn("Sample Project", content)

    def test_empty_case_returns_empty_status(self):
        """Test when no projects match: status is 'empty', no file written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = pathlib.Path(tmpdir) / "test_resume.tex"

            with patch("resume_tailor.connect", new_callable=AsyncMock) as mock_connect, \
                 patch("resume_tailor.retrieval.select_projects", new_callable=AsyncMock) as mock_select:

                # Configure mocks
                fake_conn = self._make_fake_conn()
                mock_connect.return_value = fake_conn
                mock_select.return_value = []  # No projects matched

                # Run the pipeline
                result = asyncio.run(
                    resume_tailor.tailor_resume(
                        self.minimal_job_spec,
                        user_id="test_user",
                        candidate_name="Test Candidate",
                        output_path=str(output_path),
                        gemini_api_key="test_key",
                        database_url="postgresql://test",
                    )
                )

                # Assert empty status
                self.assertEqual(result["status"], "empty")
                self.assertIsNone(result["error"])
                self.assertEqual(result["selected_projects"], [])
                self.assertEqual(result["tex_chars"], 0)

                # Assert file was NOT written
                self.assertFalse(output_path.exists())

    def test_missing_gemini_api_key_returns_error(self):
        """Test when GEMINI_API_KEY is not provided: returns error status."""
        result = asyncio.run(
            resume_tailor.tailor_resume(
                self.minimal_job_spec,
                user_id="test_user",
                candidate_name="Test Candidate",
                output_path="dummy.tex",
                gemini_api_key=None,  # Not provided
                database_url="postgresql://test",
            )
        )

        # Assert error status
        self.assertEqual(result["status"], "error")
        self.assertIn("GEMINI_API_KEY", result["error"])
        self.assertEqual(result["selected_projects"], [])
        self.assertEqual(result["tex_chars"], 0)

    def test_missing_database_url_returns_error(self):
        """Test when DATABASE_URL is not provided: returns error status."""
        result = asyncio.run(
            resume_tailor.tailor_resume(
                self.minimal_job_spec,
                user_id="test_user",
                candidate_name="Test Candidate",
                output_path="dummy.tex",
                gemini_api_key="test_key",
                database_url=None,  # Not provided
            )
        )

        # Assert error status
        self.assertEqual(result["status"], "error")
        self.assertIn("DATABASE_URL", result["error"])
        self.assertEqual(result["selected_projects"], [])
        self.assertEqual(result["tex_chars"], 0)

    def test_invalid_job_spec_returns_error(self):
        """Test when job specification is invalid: returns error status."""
        invalid_spec = {"Missing": "Title"}  # No Title key

        result = asyncio.run(
            resume_tailor.tailor_resume(
                invalid_spec,
                user_id="test_user",
                candidate_name="Test Candidate",
                output_path="dummy.tex",
                gemini_api_key="test_key",
                database_url="postgresql://test",
            )
        )

        # Assert error status
        self.assertEqual(result["status"], "error")
        self.assertIn("job specification", result["error"].lower())
        self.assertEqual(result["job_title"], "")
        self.assertEqual(result["selected_projects"], [])
        self.assertEqual(result["tex_chars"], 0)


class TestLoadJobSpec(unittest.TestCase):
    """Test the _load_job_spec helper function."""

    def test_load_job_spec_from_inline_json(self):
        """Test loading a job spec from inline JSON."""
        json_str = '{"Title": "Software Engineer", "Technologies": ["Python"]}'
        spec = resume_tailor._load_job_spec(json_str)

        self.assertEqual(spec["Title"], "Software Engineer")
        self.assertEqual(spec["Technologies"], ["Python"])

    def test_load_job_spec_from_file(self):
        """Test loading a job spec from a JSON file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(
                {"Title": "Data Scientist", "Technologies": ["Python", "SQL"]},
                f,
            )
            temp_path = f.name

        try:
            spec = resume_tailor._load_job_spec(temp_path)
            self.assertEqual(spec["Title"], "Data Scientist")
            self.assertEqual(spec["Technologies"], ["Python", "SQL"])
        finally:
            pathlib.Path(temp_path).unlink()

    def test_load_job_spec_rejects_non_object(self):
        """Test that _load_job_spec rejects JSON that is not an object."""
        json_array = '["Item1", "Item2"]'

        with self.assertRaises(ValueError) as ctx:
            resume_tailor._load_job_spec(json_array)

        self.assertIn("must be a JSON object", str(ctx.exception))

    def test_load_job_spec_rejects_invalid_json(self):
        """Test that _load_job_spec rejects invalid JSON."""
        invalid_json = '{"Title": "Incomplete"'

        with self.assertRaises(ValueError) as ctx:
            resume_tailor._load_job_spec(invalid_json)

        self.assertIn("Invalid JSON", str(ctx.exception))


class TestBuildParser(unittest.TestCase):
    """Test the _build_parser helper function."""

    def test_parser_requires_job_spec(self):
        """Test that the parser requires a job_spec positional argument."""
        parser = resume_tailor._build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_parser_accepts_all_options(self):
        """Test that the parser accepts all documented options."""
        parser = resume_tailor._build_parser()

        args = parser.parse_args([
            '{"Title": "Test"}',
            "--user-id", "user123",
            "--candidate-name", "Jane Doe",
            "--output", "/path/to/resume.tex",
            "--model", "custom-model",
            "--top-k", "5",
            "--threshold", "0.75",
            "--json-out", "/path/to/result.json",
        ])

        self.assertEqual(args.job_spec, '{"Title": "Test"}')
        self.assertEqual(args.user_id, "user123")
        self.assertEqual(args.candidate_name, "Jane Doe")
        self.assertEqual(args.output, "/path/to/resume.tex")
        self.assertEqual(args.model, "custom-model")
        self.assertEqual(args.top_k, 5)
        self.assertEqual(args.threshold, 0.75)
        self.assertEqual(args.json_out, "/path/to/result.json")

    def test_parser_default_values(self):
        """Test that the parser provides correct default values."""
        parser = resume_tailor._build_parser()

        args = parser.parse_args(['{"Title": "Test"}'])

        self.assertIsNone(args.user_id)
        self.assertEqual(args.candidate_name, "Candidate")
        self.assertEqual(args.output, "tailored_resume.tex")
        self.assertEqual(args.top_k, 4)
        self.assertEqual(args.threshold, 0.8)
        self.assertIsNone(args.json_out)


if __name__ == "__main__":
    unittest.main()
