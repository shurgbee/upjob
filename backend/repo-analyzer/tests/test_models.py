import pytest
from pydantic import ValidationError

from repo_analyzer.models import ResumeAnalysis, SkillEvaluation


def test_skill_evaluation_contract() -> None:
    output = SkillEvaluation(
        Passed_all_criteria=True,
        Score=100,
        Passed_criteria=["Implemented routing (src/router.py)"],
        Failed_criteria=[],
        Actionable_Feedback="Add load tests.",
    )
    assert output.Score == 100


def test_inconsistent_pass_flag_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillEvaluation(
            Passed_all_criteria=True,
            Score=80,
            Passed_criteria=["Implemented routing"],
            Failed_criteria=["No tests"],
            Actionable_Feedback="Add tests.",
        )


def test_resume_contract_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ResumeAnalysis(
            Summary="A tool.",
            Architectures=[],
            Technologies=[],
            Actions=[],
            Metrics=[],
            Evidence=[],
        )
