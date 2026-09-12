"""Two-pass LLM chain for generating and reviewing resume bullets.

Pass 1 generates Google-XYZ format bullets for each project; Pass 2 reviews
and corrects them. Uses gemini-3.5-flash-lite via common.gemini.generate_json.
"""

from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # resume-tailor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend

import asyncio
from typing import Any, Sequence

from schemas import (
    JobSpecification,
    SelectedProject,
    BULLETS_SCHEMA,
    BULLETS_PER_PROJECT,
    clean_bullets,
    DEFAULT_GEMINI_MODEL,
)

# System prompts copied verbatim from ResumeTailor.md sections 3 & 4
SYSTEM_PROMPT_GENERATE = (
    "You are an expert technical resume writer. Generate 3 bullet points for "
    "each provided project using the Google XYZ format: 'Accomplished [X] as "
    "measured by [Y] by doing [Z]'. Start each bullet with a strong action "
    "verb, include a clear metric, and detail the specific action or skill "
    "used. Output strictly as a JSON array of strings."
)

SYSTEM_PROMPT_REVIEW = (
    "You are a strict resume reviewer. Review the provided resume bullets and "
    "correct the following common failures: 1) Remove all first-person "
    "pronouns (I, me, my). 2) Replace weak verbs like 'helped', 'worked on', "
    "or 'assisted' with strong action verbs. 3) Remove unnecessary articles to "
    "maximize conciseness. 4) Ensure every single bullet contains a "
    "quantifiable metric. Output the corrected bullets strictly as a JSON "
    "array of strings."
)

DEFAULT_MAX_CONCURRENCY = 4


def build_generation_prompt(
    job: JobSpecification,
    project: SelectedProject,
    n: int = BULLETS_PER_PROJECT,
) -> str:
    """Compose the user content for pass 1: generation of bullet points.

    Includes the target job (title, technologies, architecture) and the project
    context (name, technologies, architectures, details_markdown). Asks for
    exactly n bullets for this project. The project's details_markdown is
    included verbatim as the primary source material.
    """
    return (
        f"## Target Job\n\n"
        f"**Title:** {job.title}\n\n"
        f"**Technologies:** {', '.join(job.technologies) if job.technologies else 'None specified'}\n\n"
        f"**Architecture:** {', '.join(job.architecture) if job.architecture else 'None specified'}\n\n"
        f"## Project\n\n"
        f"**Name:** {project.name}\n\n"
        f"**Technologies:** {', '.join(project.technologies) if project.technologies else 'None specified'}\n\n"
        f"**Architectures:** {', '.join(project.architectures) if project.architectures else 'None specified'}\n\n"
        f"## Project Details\n\n"
        f"{project.details_markdown}\n\n"
        f"## Task\n\n"
        f"Generate exactly {n} resume bullet points for this project in the Google XYZ format."
    )


def build_review_prompt(bullets: Sequence[str]) -> str:
    """Compose the user content for pass 2: review and correction of bullets.

    Takes the bullets as a numbered list to be corrected.
    """
    if not bullets:
        return "No bullets to review."

    numbered = "\n".join(f"{i + 1}. {bullet}" for i, bullet in enumerate(bullets))
    return f"Review and correct the following resume bullets:\n\n{numbered}"


async def generate_bullets(
    ai_client: Any,
    job: JobSpecification,
    project: SelectedProject,
    *,
    model: str = DEFAULT_GEMINI_MODEL,
    n: int = BULLETS_PER_PROJECT,
) -> list[str]:
    """Generate bullet points for a project using pass 1.

    Args:
        ai_client: A genai.Client.aio instance
        job: The target job specification
        project: The selected project to generate bullets for
        model: Gemini model to use (default: DEFAULT_GEMINI_MODEL)
        n: Number of bullets to generate (default: BULLETS_PER_PROJECT)

    Returns:
        A list of cleaned bullet strings
    """
    from common.gemini import generate_json

    prompt = build_generation_prompt(job, project, n)
    result = await generate_json(
        ai_client,
        model=model,
        prompt=prompt,
        schema=BULLETS_SCHEMA,
        system_instruction=SYSTEM_PROMPT_GENERATE,
    )
    return clean_bullets(result)


async def review_bullets(
    ai_client: Any,
    bullets: Sequence[str],
    *,
    model: str = DEFAULT_GEMINI_MODEL,
) -> list[str]:
    """Review and correct bullet points using pass 2.

    If the model returns an empty list, falls back to the original bullets
    to avoid silently dropping all bullets.

    Args:
        ai_client: A genai.Client.aio instance
        bullets: The bullets to review
        model: Gemini model to use (default: DEFAULT_GEMINI_MODEL)

    Returns:
        A list of reviewed/corrected bullet strings, or originals if review
        returns empty
    """
    if not bullets:
        return []

    from common.gemini import generate_json

    prompt = build_review_prompt(bullets)
    result = await generate_json(
        ai_client,
        model=model,
        prompt=prompt,
        schema=BULLETS_SCHEMA,
        system_instruction=SYSTEM_PROMPT_REVIEW,
    )
    reviewed = clean_bullets(result)
    return reviewed if reviewed else list(bullets)


async def tailor_project(
    ai_client: Any,
    job: JobSpecification,
    project: SelectedProject,
    *,
    model: str = DEFAULT_GEMINI_MODEL,
) -> SelectedProject:
    """Tailor a project by generating and reviewing its bullets.

    Runs pass 1 (generation) followed by pass 2 (review) and sets the
    project's .bullets field.

    Args:
        ai_client: A genai.Client.aio instance
        job: The target job specification
        project: The selected project to tailor
        model: Gemini model to use (default: DEFAULT_GEMINI_MODEL)

    Returns:
        The project with .bullets set
    """
    bullets = await generate_bullets(ai_client, job, project, model=model)
    reviewed = await review_bullets(ai_client, bullets, model=model)
    project.bullets = reviewed
    return project


async def generate_all(
    job: JobSpecification,
    projects: list[SelectedProject],
    *,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[SelectedProject]:
    """Generate and review bullets for all projects concurrently.

    Lazily imports google.genai and constructs a client. Runs tailor_project
    for every project under an asyncio.Semaphore for concurrency control,
    preserving input order. Cleans up the client in a finally block.

    Args:
        job: The target job specification
        projects: List of selected projects to tailor
        api_key: Gemini API key
        model: Gemini model to use (default: DEFAULT_GEMINI_MODEL)
        max_concurrency: Maximum concurrent projects (default: DEFAULT_MAX_CONCURRENCY)

    Returns:
        The projects list (same objects, now with .bullets set)

    Raises:
        RuntimeError: If google-genai is not installed
    """
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError(
            "google-genai is not installed. Install it with: pip install google-genai"
        ) from e

    client = genai.Client(api_key=api_key)
    ai_client = client.aio

    try:
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _tailor_with_semaphore(project: SelectedProject) -> SelectedProject:
            async with semaphore:
                return await tailor_project(ai_client, job, project, model=model)

        results = await asyncio.gather(
            *[_tailor_with_semaphore(p) for p in projects]
        )
        return results
    finally:
        if hasattr(ai_client, "aclose"):
            await ai_client.aclose()
