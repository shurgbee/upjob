"""Strict output contracts for repository analyses."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictOutput(BaseModel):
    """Base model that rejects fields outside the requested response schema."""

    model_config = ConfigDict(extra="forbid")


class SkillEvaluation(StrictOutput):
    Passed_all_criteria: bool
    Score: int = Field(ge=0, le=100)
    Passed_criteria: list[str]
    Failed_criteria: list[str]
    Actionable_Feedback: str

    @model_validator(mode="after")
    def validate_pass_flag(self) -> "SkillEvaluation":
        expected = bool(self.Passed_criteria) and not self.Failed_criteria
        if self.Passed_all_criteria != expected:
            raise ValueError(
                "Passed_all_criteria must be true exactly when at least one criterion passed "
                "and Failed_criteria is empty"
            )
        return self


class ResumeAnalysis(StrictOutput):
    Summary: str = Field(min_length=1)
    Architectures: list[str]
    Technologies: list[str]
    Actions: list[str]
    Metrics: list[str]
