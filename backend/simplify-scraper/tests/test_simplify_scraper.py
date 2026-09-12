import unittest
import uuid
from types import SimpleNamespace

from simplify_scraper import (
    AI_EXTRACTION_SCHEMA,
    FeedPosting,
    _error_result,
    _limit_html,
    discover_new,
    extract_job_details_with_ai,
    parse_feed,
)


FEED = """
## 💻 Software Engineering Internship Roles
<table><thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr></thead>
<tbody>
<tr><td>🔥 <strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td><td>Backend Intern 🛂</td><td>Austin, TX<br>Remote</td><td><a href="https://jobs.example/1?utm_source=Simplify&token=ok">Apply</a> <a href="https://simplify.jobs/p/abc?utm_source=GHList">Simplify</a></td><td>0d</td></tr>
<tr><td>↳</td><td>Frontend Intern</td><td>NYC</td><td><a href="https://jobs.example/2?ref=Simplify">Apply</a></td><td>2d</td></tr>
</tbody></table>
## 📈 Quantitative Finance Internship Roles
<table><thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr></thead>
<tbody><tr><td>Quant Co</td><td>Quant Intern</td><td>Chicago, IL</td><td><a href="https://jobs.example/3">Apply</a></td><td>1d</td></tr></tbody></table>
"""


class ScraperTests(unittest.TestCase):
    def test_parse_feed_handles_continuations_flags_locations_and_categories(self):
        postings = parse_feed(FEED)
        self.assertEqual(len(postings), 3)
        self.assertEqual(postings[0].company, "Acme")
        self.assertEqual(postings[0].category, "Software Engineering")
        self.assertEqual(postings[0].locations, ["Austin, TX", "Remote"])
        self.assertEqual(
            postings[0].application_url, "https://jobs.example/1?token=ok"
        )
        self.assertEqual(postings[0].simplify_url, "https://simplify.jobs/p/abc")
        self.assertEqual(postings[0].flags, ["faang_plus", "no_sponsorship"])
        self.assertEqual(postings[1].company, "Acme")
        self.assertEqual(postings[2].category, "Quantitative Finance")

    def test_parse_feed_reads_multiple_tables_in_one_category(self):
        table = """<table><tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
<tr><td>{company}</td><td>ML Intern</td><td>Remote</td><td><a href="https://jobs.example/{job}">Apply</a></td><td>0d</td></tr></table>"""
        combined = "## 🤖 Data Science, AI & Machine Learning Internship Roles\n"
        combined += table.format(company="One", job="one")
        combined += "\n---\n## Preview cutoff (not a category)\n"
        combined += table.format(company="Two", job="two")
        postings = parse_feed(combined)
        self.assertEqual(len(postings), 2)
        self.assertTrue(
            all(
                posting.category == "Data Science, AI & Machine Learning"
                for posting in postings
            )
        )

    def test_discover_new_uses_age_on_first_run_and_identity_afterward(self):
        postings = parse_feed(FEED)
        self.assertEqual(
            [item.application_url for item in discover_new(postings, None, 0)],
            ["https://jobs.example/1?token=ok"],
        )
        self.assertEqual(
            [item.application_url for item in discover_new(postings, None, 1)],
            ["https://jobs.example/1?token=ok", "https://jobs.example/3"],
        )
        seen = {"https://jobs.example/1?token=ok", "https://jobs.example/2"}
        self.assertEqual(discover_new(postings, seen), [postings[2]])


    def test_html_limit_keeps_both_ends(self):
        html = "A" * 8_000 + "B" * 8_000
        limited = _limit_html(html, 10_000)
        self.assertTrue(limited.startswith("A" * 1_000))
        self.assertTrue(limited.endswith("B" * 1_000))
        self.assertIn("HTML TRUNCATED", limited)


class _FakeModels:
    def __init__(self, parsed):
        self.parsed = parsed
        self.call = None

    async def generate_content(self, **kwargs):
        self.call = kwargs
        return SimpleNamespace(parsed=self.parsed, text=None)


class _FakeAIClient:
    def __init__(self, parsed):
        self.models = _FakeModels(parsed)


class AIExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_html_extraction_matches_sample_shape(self):
        posting = parse_feed(FEED)[0]
        extracted = {
            "company": "Acme Inc.",
            "role": "Software Engineering Intern",
            "date_posted": "2026-09-12",
            "valid_through": None,
            "employment_type": "Internship",
            "description": "Build reliable APIs.",
            "requirements": ["Currently pursuing a CS degree", "Python"],
            "skills": ["Python", "FastAPI", "Python"],
        }
        client = _FakeAIClient(extracted)
        result = await extract_job_details_with_ai(
            ai_client=client,
            posting=posting,
            page_html="<html><body><h1>Software Intern</h1></body></html>",
            scraped_url="https://jobs.example/1",
            model="gemini-test",
        )

        self.assertEqual(
            set(result),
            {
                "id",
                "company",
                "role",
                "category",
                "application_url",
                "age_days",
                "scraped_url",
                "date_posted",
                "valid_through",
                "employment_type",
                "description",
                "requirements",
                "skills",
                "scrape_status",
                "error",
            },
        )
        self.assertEqual(uuid.UUID(result["id"]).version, 4)
        self.assertEqual(result["company"], "Acme Inc.")
        self.assertEqual(result["skills"], ["Python", "FastAPI"])
        self.assertEqual(result["scrape_status"], "ok")
        self.assertIn("<job_page_html>", client.models.call["contents"])
        self.assertEqual(client.models.call["model"], "gemini-test")
        self.assertIs(
            client.models.call["config"]["response_json_schema"],
            AI_EXTRACTION_SCHEMA,
        )

    async def test_missing_ai_company_and_role_fall_back_to_feed(self):
        posting = parse_feed(FEED)[0]
        extracted = {
            "company": "",
            "role": "",
            "date_posted": None,
            "valid_through": None,
            "employment_type": None,
            "description": "",
            "requirements": [],
            "skills": [],
        }
        result = await extract_job_details_with_ai(
            _FakeAIClient(extracted),
            posting,
            "<html></html>",
            posting.application_url,
            "gemini-test",
        )
        self.assertEqual(result["company"], posting.company)
        self.assertEqual(result["role"], posting.role)

    async def test_error_result_uses_same_shape(self):
        posting = parse_feed(FEED)[0]
        error = _error_result(posting, RuntimeError("quota"), posting.application_url)
        self.assertEqual(error["scrape_status"], "error")
        self.assertIn("RuntimeError: quota", error["error"])
        self.assertEqual(uuid.UUID(error["id"]).version, 4)


if __name__ == "__main__":
    unittest.main()
