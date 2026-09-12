"""Resume tailoring pipeline: retrieve matching projects, generate bullets, inject into LaTeX.

This module provides the Feature #2 pipeline orchestration: given a job specification
and a user ID, it retrieves matching projects from the database (by hard-skill overlap
and architecture vector similarity), generates and reviews resume bullets for each
project in a two-pass Gemini chain, and injects the results into a LaTeX resume template.

The primary entry point, tailor_resume(), is async and returns only JSON-serializable
data so it can be called directly from a FastAPI route. The CLI is a thin wrapper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))        # resume-tailor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))    # backend

from schemas import build_job_specification, DEFAULT_GEMINI_MODEL, JobSpecification, SelectedProject
import retrieval
import generation
import latex_resume
from common.db import connect


async def tailor_resume(
    job_specification: dict,
    user_id: str | None = None,
    *,
    candidate_name: str = "Candidate",
    output_path: str | pathlib.Path | None = "tailored_resume.tex",
    gemini_api_key: str | None = None,
    database_url: str | None = None,
    model: str = DEFAULT_GEMINI_MODEL,
    top_k: int = 4,
    threshold: float = 0.8,
) -> dict:
    """Tailor a resume for a target job by retrieving and generating project bullets.

    Orchestrates the full pipeline:
      1. Parse the job specification
      2. Retrieve matching projects via overlap filter and vector similarity
      3. Generate and review resume bullets for each project (two-pass Gemini chain)
      4. Render and optionally write a LaTeX resume

    Args:
        job_specification: Job spec dict with keys Title, Url, Technologies, Architecture, YOE, Publish_Date
        user_id: User ID for scoping project retrieval, or None for all projects
        candidate_name: Name to inject into the resume template
        output_path: Path to write the tailored resume .tex file, or None to skip writing
        gemini_api_key: Gemini API key; falls back to GEMINI_API_KEY env var
        database_url: PostgreSQL DSN; falls back to DATABASE_URL env var
        model: Gemini model to use (default: DEFAULT_GEMINI_MODEL)
        top_k: Number of projects to select (default: 4)
        threshold: Overlap ratio threshold for hard-skill filtering (default: 0.8)

    Returns:
        JSON-serializable envelope dict with keys:
          - status: "ok" | "empty" | "error"
          - error: Error message if status is "error", else None
          - job_title: Job title from the spec (or empty if parse failed)
          - candidate_name: Echoed candidate name
          - user_id: Echoed user ID
          - output_path: Path written to, or None if not written
          - selected_projects: List of dicts (empty if none matched or error)
          - tex_chars: Length of generated LaTeX, or 0 if not produced

        On status "ok": output_path is the written path (str), selected_projects is
        non-empty, and tex_chars > 0.

        On status "empty": no projects matched the criteria; tex is not written.

        On status "error": an exception occurred; error message is set; selected_projects
        and tex are not produced.
    """
    # Parse job spec; catch ValueError and return error envelope
    job_title = ""
    try:
        job = build_job_specification(job_specification)
        job_title = job.title
    except ValueError as e:
        return {
            "status": "error",
            "error": f"Invalid job specification: {e}",
            "job_title": "",
            "candidate_name": candidate_name,
            "user_id": user_id,
            "output_path": None,
            "selected_projects": [],
            "tex_chars": 0,
        }

    # Resolve API key and database URL from kwargs or environment
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {
            "status": "error",
            "error": "GEMINI_API_KEY is not set",
            "job_title": job_title,
            "candidate_name": candidate_name,
            "user_id": user_id,
            "output_path": None,
            "selected_projects": [],
            "tex_chars": 0,
        }

    dsn = database_url or os.getenv("DATABASE_URL")
    if not dsn:
        return {
            "status": "error",
            "error": "DATABASE_URL is not set",
            "job_title": job_title,
            "candidate_name": candidate_name,
            "user_id": user_id,
            "output_path": None,
            "selected_projects": [],
            "tex_chars": 0,
        }

    # Wrap the retrieval/generation/render body to catch exceptions
    try:
        # Connect to database
        conn = await connect(dsn)
        try:
            # Stage 1: Retrieve matching projects
            # NOTE: `model` is the GENERATION model and must not be forwarded to
            # retrieval — its embedding step uses the embedding model (and must,
            # to match the stored vectors). Leaving `model` unset lets
            # embed_job_architecture fall back to EMBEDDING_MODEL.
            selected = await retrieval.select_projects(
                conn,
                job,
                user_id,
                api_key=api_key,
                threshold=threshold,
                top_k=top_k,
            )

            # Stage 2: Check if any projects were selected
            if not selected:
                return {
                    "status": "empty",
                    "error": None,
                    "job_title": job_title,
                    "candidate_name": candidate_name,
                    "user_id": user_id,
                    "output_path": None,
                    "selected_projects": [],
                    "tex_chars": 0,
                }

            # Stage 3: Generate bullets for each project
            selected = await generation.generate_all(
                job,
                selected,
                api_key=api_key,
                model=model,
            )

            # Stage 4: Render LaTeX resume
            tex = latex_resume.render_resume(
                selected,
                candidate_name=candidate_name,
            )

            # Stage 5: Write to file if requested
            written_path = None
            if output_path:
                output_path = pathlib.Path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(tex, encoding="utf-8")
                written_path = str(output_path)

            return {
                "status": "ok",
                "error": None,
                "job_title": job_title,
                "candidate_name": candidate_name,
                "user_id": user_id,
                "output_path": written_path,
                "selected_projects": [p.to_dict() for p in selected],
                "tex_chars": len(tex),
            }

        finally:
            await conn.close()

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "job_title": job_title,
            "candidate_name": candidate_name,
            "user_id": user_id,
            "output_path": None,
            "selected_projects": [],
            "tex_chars": 0,
        }


def _load_job_spec(raw: str) -> dict:
    """Load a job specification from JSON or a file path.

    If raw is a path to an existing file, read and parse it as JSON. Otherwise,
    parse raw directly as JSON.

    Args:
        raw: Either a JSON string or a file path

    Returns:
        Parsed JSON object (dict)

    Raises:
        ValueError: If JSON is invalid or the result is not an object
    """
    # Check if raw is a path to an existing file
    path = pathlib.Path(raw)
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            raise ValueError(f"Failed to read file {raw}: {e}") from e
    else:
        content = raw

    # Parse JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    # Ensure it's a dict
    if not isinstance(parsed, dict):
        raise ValueError("job specification must be a JSON object")

    return parsed


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        ArgumentParser configured with the resume-tailor CLI options
    """
    parser = argparse.ArgumentParser(
        description="Tailor a resume for a target job.",
        prog="resume_tailor",
    )

    parser.add_argument(
        "job_spec",
        help="Job specification: inline JSON or path to a JSON file",
    )

    parser.add_argument(
        "--user-id",
        default=None,
        help="User ID for scoping project retrieval (optional)",
    )

    parser.add_argument(
        "--candidate-name",
        default="Candidate",
        help="Candidate name to inject into the resume (default: Candidate)",
    )

    parser.add_argument(
        "--output",
        default="tailored_resume.tex",
        help="Output path for the tailored resume LaTeX file (default: tailored_resume.tex)",
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model to use (default: {DEFAULT_GEMINI_MODEL})",
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=4,
        help="Number of projects to select (default: 4)",
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=0.8,
        help="Overlap ratio threshold for hard-skill filtering (default: 0.8)",
    )

    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write the result envelope as JSON",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the resume tailor.

    Loads environment variables via python-dotenv if available (best-effort),
    parses arguments, loads the job specification, runs the tailor_resume pipeline,
    and outputs the result as JSON.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        0 if status is "ok", 1 otherwise
    """
    # Try to load .env via python-dotenv if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Load job spec
    try:
        job_spec = _load_job_spec(args.job_spec)
    except ValueError as e:
        print(f"Error loading job specification: {e}", file=sys.stderr)
        return 1

    # Run the pipeline
    result = asyncio.run(
        tailor_resume(
            job_spec,
            user_id=args.user_id,
            candidate_name=args.candidate_name,
            output_path=args.output,
            model=args.model,
            top_k=args.top_k,
            threshold=args.threshold,
        )
    )

    # Output result
    if args.json_out:
        pathlib.Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.json_out).write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(result, indent=2))

    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
