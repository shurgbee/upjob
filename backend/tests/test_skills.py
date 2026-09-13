from __future__ import annotations

import unittest

import main  # noqa: F401 - initializes the local repo-analyzer import path
from skills_service import build_skill_gaps, normalize_competency
from skills_worker import QuestPlan, validate_plan


class SkillGapTests(unittest.TestCase):
    def test_normalization_collapses_case_and_spacing(self) -> None:
        self.assertEqual(normalize_competency("  AWS   Lambda "), "aws lambda")

    def test_gap_counts_each_skill_once_per_job_and_excludes_known(self) -> None:
        jobs = [
            {"technologies": ["Python", "React", "react"], "architecture": ["REST API"]},
            {"technologies": ["React", "PostgreSQL"], "architecture": ["REST  API"]},
        ]
        projects = [{"technologies": [" python "], "architecture": []}]
        passed = [{"target_skills": ["PostgreSQL"]}]

        gaps, known_count = build_skill_gaps(jobs, projects, passed)

        self.assertEqual(known_count, 2)
        self.assertEqual(
            gaps,
            [
                {"name": "React", "job_count": 2},
                {"name": "REST API", "job_count": 2},
            ],
        )

    def test_gap_order_is_deterministic_for_equal_counts(self) -> None:
        gaps, _ = build_skill_gaps(
            [{"technologies": ["TypeScript", "Docker"], "architecture": []}],
            [],
            [],
        )
        self.assertEqual([gap["name"] for gap in gaps], ["Docker", "TypeScript"])


class QuestPlanTests(unittest.TestCase):
    def valid_plan(self) -> dict:
        return {
            "quests": [
                {
                    "title": f"Project {number}",
                    "description": "Build a practical project with clear repository evidence.",
                    "target_skills": [skill],
                    "learning_objective": f"Demonstrate applied {skill} through tested implementation evidence.",
                    "acceptance_criteria": ["Working implementation", "Automated tests", "Setup documentation"],
                }
                for number, skill in enumerate(["React", "Docker", "REST API"], 1)
            ]
        }

    def test_valid_plan_requires_exactly_three_projects(self) -> None:
        plan = validate_plan(
            self.valid_plan(),
            [{"name": name, "job_count": 2} for name in ["React", "Docker", "REST API"]],
        )
        self.assertIsInstance(plan, QuestPlan)
        self.assertEqual(len(plan.quests), 3)

    def test_plan_cannot_invent_target_skills(self) -> None:
        raw = self.valid_plan()
        raw["quests"][0]["target_skills"] = ["Kubernetes"]
        with self.assertRaisesRegex(ValueError, "unavailable skill"):
            validate_plan(
                raw,
                [{"name": name, "job_count": 2} for name in ["React", "Docker", "REST API"]],
            )


if __name__ == "__main__":
    unittest.main()
