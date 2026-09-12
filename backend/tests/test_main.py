from __future__ import annotations

import io
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import simplify_scraper
from repo_analyzer.models import ResumeAnalysis, SkillEvaluation

import main


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FeedLocator:
    async def inner_text(self, *, timeout: int) -> str:
        del timeout
        return """
## Software Engineering Internship Roles
<table>
<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
<tr><td>Acme</td><td>API Intern</td><td>Remote</td><td><a href="https://jobs.example/1">Apply</a></td><td>0d</td></tr>
</table>
"""


class _FeedPage:
    async def goto(self, *args, **kwargs) -> None:
        del args, kwargs

    def locator(self, selector: str) -> _FeedLocator:
        assert selector == "body"
        return _FeedLocator()

    async def close(self) -> None:
        pass


class _Browser:
    async def new_page(self) -> _FeedPage:
        return _FeedPage()

    async def close(self) -> None:
        pass


class _GenAIClient:
    aio = None

    def __init__(self, *, api_key: str) -> None:
        assert api_key == "test-key"
        self.aio = self

    async def aclose(self) -> None:
        pass


class ConfigurationTests(unittest.TestCase):
    def test_repository_requests_use_requested_default(self) -> None:
        skill = main.SkillAnalysisRequest(learning_objective="Learn LangGraph")
        resume = main.ResumeAnalysisRequest()

        self.assertEqual(skill.repository, "PyroSh0ck/miniProjects-langGraph")
        self.assertEqual(resume.repository, "PyroSh0ck/miniProjects-langGraph")

    def test_live_logger_uses_event_color_in_a_terminal(self) -> None:
        output = _TTYBuffer()
        with patch.object(main.sys, "stderr", output):
            main._write_colored_analyzer_event(
                {"event": "tool_call", "tool": "search_code"}
            )

        self.assertIn("\033[95m", output.getvalue())
        self.assertIn("repo-analyzer:tool_call", output.getvalue())
        self.assertIn("search_code", output.getvalue())


class ScraperModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_scraper_always_passes_gemini_3_5_flash(self) -> None:
        scraped_job = {
            "id": "test-id",
            "company": "Acme",
            "role": "API Intern",
            "category": "Software Engineering",
            "application_url": "https://jobs.example/1",
            "age_days": 0,
            "scraped_url": "https://jobs.example/1",
            "date_posted": None,
            "valid_through": None,
            "employment_type": "Internship",
            "description": "Build APIs.",
            "requirements": [],
            "skills": [],
            "scrape_status": "ok",
            "error": None,
        }
        scrape_one = AsyncMock(return_value=scraped_job)

        with (
            patch("cloakbrowser.launch_async", AsyncMock(return_value=_Browser())),
            patch("google.genai.Client", _GenAIClient),
            patch.object(simplify_scraper, "_scrape_one", scrape_one),
        ):
            result = await simplify_scraper.scrape_new_jobs(
                state_file=None,
                max_jobs=1,
                gemini_api_key="test-key",
            )

        self.assertEqual(result["new_posting_count"], 1)
        self.assertEqual(scrape_one.await_args.kwargs["model"], "gemini-3.5-flash")


class APITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_skill_endpoint_uses_default_repo_and_enables_logs(self) -> None:
        analyzer = unittest.mock.Mock()
        analyzer.evaluate_skill = AsyncMock(
            return_value=SkillEvaluation(
                Passed_all_criteria=False,
                Score=50,
                Passed_criteria=["API exists"],
                Failed_criteria=["Tests missing"],
                Actionable_Feedback="Add tests.",
            )
        )

        with patch.object(main, "_analyzer", return_value=analyzer) as factory:
            response = await self.client.post(
                "/repo-analyzer/skill",
                json={"learning_objective": "Build an API", "show_logs": True},
            )

        self.assertEqual(response.status_code, 200)
        factory.assert_called_once_with(True)
        analyzer.evaluate_skill.assert_awaited_once_with(
            "PyroSh0ck/miniProjects-langGraph",
            "Build an API",
        )

    async def test_resume_endpoint_uses_default_repo(self) -> None:
        analyzer = unittest.mock.Mock()
        analyzer.extract_resume_material = AsyncMock(
            return_value=ResumeAnalysis(
                Summary="A project.",
                Architectures=[],
                Technologies=[],
                Actions=[],
                Metrics=[],
            )
        )

        with patch.object(main, "_analyzer", return_value=analyzer):
            response = await self.client.post("/repo-analyzer/resume", json={})

        self.assertEqual(response.status_code, 200)
        analyzer.extract_resume_material.assert_awaited_once_with(
            "PyroSh0ck/miniProjects-langGraph"
        )

    async def test_scraper_model_can_no_longer_be_overridden(self) -> None:
        response = await self.client.post(
            "/simplify-scraper/jobs",
            json={"gemini_model": "another-model"},
        )

        self.assertEqual(response.status_code, 422)

    async def test_overlapping_scraper_request_is_rejected_not_queued(self) -> None:
        await main._scraper_lock.acquire()
        try:
            response = await self.client.post("/simplify-scraper/jobs", json={})
        finally:
            main._scraper_lock.release()

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
