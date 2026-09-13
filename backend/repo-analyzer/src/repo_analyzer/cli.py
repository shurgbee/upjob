"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from dotenv import load_dotenv
from google.genai.errors import APIError
from mcp import MCPError
from pydantic import ValidationError

from .analyzer import RepositoryAnalysisError, RepositoryAnalyzer
from .config import ConfigurationError, Settings
from .repository import PublicRepositoryRequired, RepositoryReferenceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-analyzer",
        description="Analyze a public GitHub repository through GitHub MCP.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    skill = subparsers.add_parser("skill", help="Evaluate a learning objective")
    skill.add_argument("--repo", required=True, help="owner/repo or GitHub HTTPS URL")
    skill.add_argument("--objective", required=True, help="Learning objective / acceptance criteria")
    skill.add_argument(
        "--show-conversation",
        action="store_true",
        help="Stream Gemini and GitHub MCP events as JSONL to stderr",
    )

    resume = subparsers.add_parser("resume", help="Extract resume bullet source material")
    resume.add_argument("--repo", required=True, help="owner/repo or GitHub HTTPS URL")
    resume.add_argument(
        "--show-conversation",
        action="store_true",
        help="Stream Gemini and GitHub MCP events as JSONL to stderr",
    )
    return parser


def _write_conversation_event(event: dict) -> None:
    record = {"timestamp": datetime.now(UTC).isoformat(), **event}
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


async def _execute(args: argparse.Namespace) -> str:
    callback = _write_conversation_event if args.show_conversation else None
    analyzer = RepositoryAnalyzer(Settings.from_env(), conversation_callback=callback)
    if args.command == "skill":
        output = await analyzer.evaluate_skill(args.repo, args.objective)
    else:
        output = await analyzer.extract_resume_material(args.repo)
    return output.model_dump_json(indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        print(asyncio.run(_execute(args)))
        return 0
    except (
        ConfigurationError,
        PublicRepositoryRequired,
        RepositoryAnalysisError,
        RepositoryReferenceError,
        ValidationError,
        ValueError,
        APIError,
        MCPError,
    ) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "Analysis cancelled"}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
