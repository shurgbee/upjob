"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is absent or invalid."""


def _required_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    joined = " or ".join(names)
    raise ConfigurationError(f"Set {joined} in the environment or .env file")


@dataclass(frozen=True, slots=True)
class Settings:
    gemini_api_key: str
    github_pat: str
    model: str = "gemini-3.8-flash"
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    max_turns: int = 100

    @classmethod
    def from_env(cls) -> Settings:
        try:
            max_turns = int(os.getenv("REPO_ANALYZER_MAX_TURNS", "100"))
        except ValueError as exc:
            raise ConfigurationError(
                "REPO_ANALYZER_MAX_TURNS must be an integer"
            ) from exc
        if max_turns < 5:
            raise ConfigurationError("REPO_ANALYZER_MAX_TURNS must be at least 5")

        return cls(
            gemini_api_key=_required_env("GEMINI_API_KEY"),
            github_pat=_required_env("GITHUB_PAT", "GITHUB_TOKEN"),
            model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
            github_mcp_url=os.getenv(
                "GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/"
            ).strip(),
            max_turns=max_turns,
        )
