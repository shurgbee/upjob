"""Unit tests for the generation module (two-pass LLM chain).

Offline tests with stubbed Gemini SDK and no network calls.
"""

from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # resume-tailor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))  # backend

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from schemas import JobSpecification, SelectedProject, BULLETS_PER_PROJECT
import generation


class FakeModels:
    """Fake models object to hold the generate_content method."""

    def __init__(self, ai_client: "FakeAIClient"):
        self.ai_client = ai_client

    async def generate_content(
        self, *, model: str, contents: str, config: dict
    ) -> Any:
        """Record the call and return a fake response."""
        self.ai_client.last_system_instruction = config.get("system_instruction")
        self.ai_client.last_prompt = contents
        self.ai_client.calls.append(
            {"model": model, "contents": contents, "config": config}
        )

        response = MagicMock()
        response.parsed = {
            "bullets": [
                "Accomplished X as measured by Y by doing Z"
            ] * BULLETS_PER_PROJECT
        }
        response.text = None
        return response


class FakeAIClient:
    """Fake ai_client that records calls and returns a fixed response.

    Used to test generation functions without the real Gemini SDK.
    Mimics the structure of genai.Client.aio: ai_client.models.generate_content(...).
    """

    def __init__(self):
        self.calls = []
        self.last_system_instruction = None
        self.last_prompt = None
        # Create a fake models object with async generate_content method
        self.models = FakeModels(self)

    async def aclose(self):
        """No-op close for the fake client."""
        pass


class TestPromptBuilders(unittest.TestCase):
    """Test prompt building functions."""

    def setUp(self):
        self.job = JobSpecification(
            title="Senior Backend Engineer",
            technologies=["Python", "FastAPI"],
            architecture=["Microservices", "REST APIs"],
        )
        self.project = SelectedProject(
            project_id=1,
            name="API Gateway",
            technologies=["Python", "FastAPI"],
            architectures=["REST"],
            details_markdown="# API Gateway\n\nBuilt a fast API gateway with caching.",
        )

    def test_build_generation_prompt_includes_job_title(self):
        """build_generation_prompt includes the job title."""
        prompt = generation.build_generation_prompt(self.job, self.project)
        self.assertIn(self.job.title, prompt)

    def test_build_generation_prompt_includes_details_markdown(self):
        """build_generation_prompt includes the project's details_markdown."""
        prompt = generation.build_generation_prompt(self.job, self.project)
        self.assertIn(self.project.details_markdown, prompt)

    def test_build_generation_prompt_includes_project_name(self):
        """build_generation_prompt includes the project name."""
        prompt = generation.build_generation_prompt(self.job, self.project)
        self.assertIn(self.project.name, prompt)

    def test_build_generation_prompt_includes_job_technologies(self):
        """build_generation_prompt includes job technologies."""
        prompt = generation.build_generation_prompt(self.job, self.project)
        for tech in self.job.technologies:
            self.assertIn(tech, prompt)

    def test_build_generation_prompt_custom_n(self):
        """build_generation_prompt respects custom n."""
        prompt = generation.build_generation_prompt(self.job, self.project, n=5)
        self.assertIn("5 resume bullet points", prompt)

    def test_build_review_prompt_includes_bullet_text(self):
        """build_review_prompt includes the bullet text."""
        bullets = ["First bullet", "Second bullet"]
        prompt = generation.build_review_prompt(bullets)
        self.assertIn("First bullet", prompt)
        self.assertIn("Second bullet", prompt)

    def test_build_review_prompt_empty(self):
        """build_review_prompt handles empty bullets gracefully."""
        prompt = generation.build_review_prompt([])
        self.assertIsInstance(prompt, str)


class TestGenerateBullets(unittest.TestCase):
    """Test the generate_bullets async function."""

    def setUp(self):
        self.job = JobSpecification(
            title="Backend Engineer", technologies=["Python"], architecture=[]
        )
        self.project = SelectedProject(
            project_id=1,
            name="API",
            technologies=["Python"],
            architectures=[],
            details_markdown="Built an API.",
        )

    def test_generate_bullets_returns_list(self):
        """generate_bullets returns a list of strings."""

        async def run():
            ai_client = FakeAIClient()
            result = await generation.generate_bullets(ai_client, self.job, self.project)
            self.assertIsInstance(result, list)
            self.assertTrue(all(isinstance(b, str) for b in result))

        asyncio.run(run())

    def test_generate_bullets_returns_cleaned_bullets(self):
        """generate_bullets returns cleaned (whitespace-normalized) bullets."""

        async def run():
            ai_client = FakeAIClient()
            result = await generation.generate_bullets(ai_client, self.job, self.project)
            self.assertEqual(len(result), BULLETS_PER_PROJECT)

        asyncio.run(run())

    def test_generate_bullets_uses_correct_system_prompt(self):
        """generate_bullets uses SYSTEM_PROMPT_GENERATE."""

        async def run():
            captured_system_instruction = None

            async def fake_generate_json(
                client, *, model, prompt, schema, system_instruction, **kwargs
            ):
                nonlocal captured_system_instruction
                captured_system_instruction = system_instruction
                return {"bullets": ["X", "Y", "Z"]}

            with patch("common.gemini.generate_json", fake_generate_json):
                ai_client = FakeAIClient()
                await generation.generate_bullets(ai_client, self.job, self.project)
                self.assertEqual(
                    captured_system_instruction,
                    generation.SYSTEM_PROMPT_GENERATE,
                )

        asyncio.run(run())


class TestReviewBullets(unittest.TestCase):
    """Test the review_bullets async function."""

    def test_review_bullets_empty_returns_empty(self):
        """review_bullets returns empty list for empty input."""

        async def run():
            ai_client = FakeAIClient()
            result = await generation.review_bullets(ai_client, [])
            self.assertEqual(result, [])

        asyncio.run(run())

    def test_review_bullets_fallback_to_originals(self):
        """review_bullets falls back to originals when model returns empty."""

        async def run():
            ai_client = FakeAIClient()
            original = ["Original bullet about accomplishments"]

            async def fake_generate_json(
                client, *, model, prompt, schema, system_instruction, **kwargs
            ):
                return {"bullets": []}

            with patch("common.gemini.generate_json", fake_generate_json):
                result = await generation.review_bullets(ai_client, original)
                self.assertEqual(result, original)

        asyncio.run(run())

    def test_review_bullets_returns_reviewed(self):
        """review_bullets returns reviewed bullets when model returns non-empty."""

        async def run():
            ai_client = FakeAIClient()
            original = ["Original bullet"]

            async def fake_generate_json(
                client, *, model, prompt, schema, system_instruction, **kwargs
            ):
                return {"bullets": ["Reviewed bullet"]}

            with patch("common.gemini.generate_json", fake_generate_json):
                result = await generation.review_bullets(ai_client, original)
                self.assertEqual(result, ["Reviewed bullet"])

        asyncio.run(run())

    def test_review_bullets_uses_correct_system_prompt(self):
        """review_bullets uses SYSTEM_PROMPT_REVIEW."""

        async def run():
            captured_system_instruction = None

            async def fake_generate_json(
                client, *, model, prompt, schema, system_instruction, **kwargs
            ):
                nonlocal captured_system_instruction
                captured_system_instruction = system_instruction
                return {"bullets": ["X", "Y"]}

            with patch("common.gemini.generate_json", fake_generate_json):
                ai_client = FakeAIClient()
                await generation.review_bullets(ai_client, ["bullet"])
                self.assertEqual(
                    captured_system_instruction, generation.SYSTEM_PROMPT_REVIEW
                )

        asyncio.run(run())


class TestTailorProject(unittest.TestCase):
    """Test the tailor_project async function."""

    def setUp(self):
        self.job = JobSpecification(title="Backend Engineer")
        self.project = SelectedProject(
            project_id=1, name="API", details_markdown="Built an API."
        )

    def test_tailor_project_sets_bullets(self):
        """tailor_project sets project.bullets."""

        async def run():
            ai_client = FakeAIClient()

            async def fake_generate_json(
                client, *, model, prompt, schema, system_instruction, **kwargs
            ):
                return {
                    "bullets": [
                        "Accomplished X as measured by Y by doing Z"
                    ] * 3
                }

            with patch("common.gemini.generate_json", fake_generate_json):
                result = await generation.tailor_project(ai_client, self.job, self.project)
                self.assertIsNotNone(result.bullets)
                self.assertTrue(len(result.bullets) > 0)

        asyncio.run(run())

    def test_tailor_project_returns_same_project_object(self):
        """tailor_project returns the same project object."""

        async def run():
            ai_client = FakeAIClient()

            async def fake_generate_json(
                client, *, model, prompt, schema, system_instruction, **kwargs
            ):
                return {"bullets": ["Accomplished X"] * 3}

            with patch("common.gemini.generate_json", fake_generate_json):
                result = await generation.tailor_project(ai_client, self.job, self.project)
                self.assertIs(result, self.project)

        asyncio.run(run())


class TestGenerateAll(unittest.TestCase):
    """Test the generate_all async function."""

    def setUp(self):
        self.job = JobSpecification(title="Backend Engineer")
        self.projects = [
            SelectedProject(
                project_id=i, name=f"Project {i}", details_markdown=f"Project {i}"
            )
            for i in range(3)
        ]

    def test_generate_all_preserves_order(self):
        """generate_all preserves input project order."""

        async def run():
            async def fake_tailor_project(ai_client, job, project, *, model):
                project.bullets = ["Accomplished X", "Accomplished Y", "Accomplished Z"]
                return project

            # Create a mock that properly handles the Client constructor
            mock_client = MagicMock()
            mock_client.aio = FakeAIClient()

            fake_genai_module = MagicMock()
            fake_genai_module.Client = MagicMock(return_value=mock_client)

            fake_google = MagicMock()
            fake_google.genai = fake_genai_module

            with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai_module}):
                with patch("generation.tailor_project", fake_tailor_project):
                    results = await generation.generate_all(
                        self.job, self.projects, api_key="test-key"
                    )
                    self.assertEqual(len(results), len(self.projects))
                    for i, proj in enumerate(results):
                        self.assertEqual(proj.project_id, self.projects[i].project_id)

        asyncio.run(run())

    def test_generate_all_sets_bullets_on_all_projects(self):
        """generate_all sets bullets on every project."""

        async def run():
            async def fake_tailor_project(ai_client, job, project, *, model):
                project.bullets = ["Accomplished X", "Accomplished Y", "Accomplished Z"]
                return project

            # Create a mock that properly handles the Client constructor
            mock_client = MagicMock()
            mock_client.aio = FakeAIClient()

            fake_genai_module = MagicMock()
            fake_genai_module.Client = MagicMock(return_value=mock_client)

            fake_google = MagicMock()
            fake_google.genai = fake_genai_module

            with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai_module}):
                with patch("generation.tailor_project", fake_tailor_project):
                    results = await generation.generate_all(
                        self.job, self.projects, api_key="test-key"
                    )
                    for proj in results:
                        self.assertIsNotNone(proj.bullets)
                        self.assertTrue(len(proj.bullets) > 0)

        asyncio.run(run())

    def test_generate_all_respects_max_concurrency(self):
        """generate_all respects the max_concurrency parameter."""

        async def run():
            call_count = 0
            concurrent_count = 0
            max_concurrent_observed = 0

            async def fake_tailor_project(ai_client, job, project, *, model):
                nonlocal call_count, concurrent_count, max_concurrent_observed
                call_count += 1
                concurrent_count += 1
                max_concurrent_observed = max(max_concurrent_observed, concurrent_count)
                await asyncio.sleep(0.01)
                concurrent_count -= 1
                project.bullets = ["X"]
                return project

            # Create a mock that properly handles the Client constructor
            mock_client = MagicMock()
            mock_client.aio = FakeAIClient()

            fake_genai_module = MagicMock()
            fake_genai_module.Client = MagicMock(return_value=mock_client)

            fake_google = MagicMock()
            fake_google.genai = fake_genai_module

            with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai_module}):
                with patch("generation.tailor_project", fake_tailor_project):
                    results = await generation.generate_all(
                        self.job, self.projects, api_key="test-key", max_concurrency=2
                    )
                    self.assertEqual(len(results), 3)
                    self.assertLessEqual(max_concurrent_observed, 2)

        asyncio.run(run())


class TestImports(unittest.TestCase):
    """Test that google-genai is not imported at module scope."""

    def test_google_genai_not_imported_at_module_scope(self):
        """Verify that google-genai is not imported when generation is imported."""
        # Check that genai is not in sys.modules
        self.assertNotIn("google.genai", sys.modules)
        # google package might be there for other reasons, but check genai specifically
        if "google" in sys.modules:
            # If google is present, it should not have genai yet
            google_module = sys.modules.get("google", None)
            if google_module is not None:
                self.assertFalse(hasattr(google_module, "genai"))


if __name__ == "__main__":
    unittest.main()
