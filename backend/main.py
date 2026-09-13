"""Umbrella FastAPI app for the upjob backend.

Run from this directory with::

    uvicorn main:app --reload

Each backend component keeps its own project directory and exposes either a
FastAPI router or async entry points.  This app mounts those routers and, for
repo-analyzer and simplify-scraper, defines the HTTP endpoints directly.  The
path setup below lets this module run directly from a source checkout without
first installing any component as a package.  To add a new component: append its
source directory to the ``sys.path`` bootstrap below, import its
``create_*_router`` factory, and ``app.include_router`` it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from google.genai.errors import APIError
from mcp import MCPError
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

BASE_DIR = Path(__file__).resolve().parent
REPO_ANALYZER_DIR = BASE_DIR / "repo-analyzer"
SIMPLIFY_SCRAPER_DIR = BASE_DIR / "simplify-scraper"
REWARD_HANDLER_DIR = BASE_DIR / "reward-handler"
GMAIL_AGENT_DIR = BASE_DIR / "gmail-agent"

# Components run from their own directories, so they are not importable by
# default.  Add each component source root, plus BASE_DIR for the shared
# ``common`` package that the components import.
for source_root in (
    REPO_ANALYZER_DIR / "src",
    SIMPLIFY_SCRAPER_DIR,
    REWARD_HANDLER_DIR,
    GMAIL_AGENT_DIR,
    BASE_DIR,
):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

# Load local development configuration without overriding deployment-provided
# environment variables.
for dotenv_path in (
    BASE_DIR / ".env",
    REPO_ANALYZER_DIR / ".env",
    SIMPLIFY_SCRAPER_DIR / ".env",
    REWARD_HANDLER_DIR / ".env",
    GMAIL_AGENT_DIR / ".env",
):
    load_dotenv(dotenv_path=dotenv_path, override=False)

from repo_analyzer import RepositoryAnalyzer
from repo_analyzer.analyzer import RepositoryAnalysisError
from repo_analyzer.config import ConfigurationError, Settings
from repo_analyzer.models import ResumeAnalysis, SkillEvaluation
from repo_analyzer.repository import (
    PublicRepositoryRequired,
    RepositoryRef,
    RepositoryReferenceError,
)
from simplify_scraper import (
    DEFAULT_MAX_HTML_CHARS,
    DEFAULT_SOURCE_URL,
    scrape_new_jobs,
)
from reward_handler import create_fastapi_router
from gmail_agent import create_fastapi_router as create_gmail_router
from gmail_scheduler import start_gmail_scheduler

DEFAULT_REPOSITORY = "PyroSh0ck/miniProjects-langGraph"

# reward-handler reads DATABASE_URL / REDIS_URL; the rest of the backend uses
# POSTGRES_URL.  Prefer the reward-handler names and fall back to POSTGRES_URL so
# every component targets one database.  ``None`` lets each entry point resolve
# its own environment variable (and, for Redis, fall back to the in-memory
# leaderboard when REDIS_URL is unset).
DATABASE_DSN = (
    os.getenv("DATABASE_URL", "").strip()
    or os.getenv("POSTGRES_URL", "").strip()
    or None
)
REDIS_URL = os.getenv("REDIS_URL", "").strip() or None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or None
# The gmail-agent's 2-hour poll runs in-process unless disabled (e.g. to avoid a
# live Gmail/Gemini poll in CI or local smoke tests).
GMAIL_SCHEDULER_ENABLED = os.getenv("GMAIL_SCHEDULER_ENABLED", "1").strip() not in ("0", "false", "")


class APIModel(BaseModel):
    """Base request/response model with typo detection enabled."""

    model_config = ConfigDict(extra="forbid")


class SkillAnalysisRequest(APIModel):
    repository: str = Field(
        default=DEFAULT_REPOSITORY,
        min_length=3,
        examples=[DEFAULT_REPOSITORY],
        description="Public GitHub repository as owner/name or an HTTPS URL.",
    )
    learning_objective: str = Field(
        min_length=1,
        examples=["Build a tested REST API with authentication and persistence."],
    )
    show_logs: bool = Field(
        default=False,
        description="Print live, color-coded analysis events in the API server console.",
    )


class ResumeAnalysisRequest(APIModel):
    user_id: UUID = Field(
        description="User UUID from public.app_users that owns the saved project.",
    )
    repository: str = Field(
        default=DEFAULT_REPOSITORY,
        min_length=3,
        examples=[DEFAULT_REPOSITORY],
        description="Public GitHub repository as owner/name or an HTTPS URL.",
    )
    show_logs: bool = Field(
        default=False,
        description="Print live, color-coded analysis events in the API server console.",
    )


class ScrapeJobsRequest(APIModel):
    source_url: AnyHttpUrl = Field(default=DEFAULT_SOURCE_URL)
    use_state: bool = Field(
        default=True,
        description="Persist discovered URLs so later calls return only new postings.",
    )
    first_run_max_age_days: Annotated[int, Field(ge=0)] = 0
    max_jobs: Annotated[int | None, Field(ge=0, le=100)] = 25
    concurrency: Annotated[int, Field(ge=1, le=10)] = 3
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = 45
    headless: bool = True
    proxy: str | None = Field(
        default=None,
        description="Optional CloakBrowser proxy URL.",
    )
    include_all: bool = Field(
        default=False,
        description="Ignore scraper state and consider every active posting.",
    )
    max_html_chars: Annotated[int, Field(ge=10_000, le=5_000_000)] = (
        DEFAULT_MAX_HTML_CHARS
    )


class JobPosting(APIModel):
    id: str
    company: str
    role: str
    category: str
    application_url: str
    age_days: int | None
    scraped_url: str
    date_posted: str | None
    valid_through: str | None
    employment_type: str | None
    description: str
    requirements: list[str]
    skills: list[str]
    scrape_status: Literal["ok", "error"]
    error: str | None


class JobSpec(APIModel):
    model_config = ConfigDict(extra="allow")

    title: str
    url: str
    company: str
    category: str
    employment_type: str | None
    description: str
    requirements: list[str]
    technologies: list[str]
    architecture: list[str]
    yoe: int | None
    publish_date: str | None
    spec_created_at: str | None
    spec_updated_at: str | None


class ScrapeJobsResponse(APIModel):
    generated_at: str
    source_url: str
    new_posting_count: int
    postings: list[JobPosting]
    job_specs: list[JobSpec]


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"
    services: tuple[str, ...] = (
        "repo-analyzer",
        "simplify-scraper",
        "reward-handler",
        "gmail-agent",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if GMAIL_SCHEDULER_ENABLED:
        scheduler = start_gmail_scheduler(dsn=DATABASE_DSN, gemini_api_key=GEMINI_API_KEY)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(
    title="Upjob Backend API",
    version="1.0.0",
    description="Umbrella HTTP interface for the upjob backend components.",
    lifespan=lifespan,
)

from resume_service import router as resume_router
app.include_router(resume_router)
from skills_service import router as skills_router
app.include_router(skills_router)

# reward-handler: economy, verification, leaderboard, and cron routes under /api.
app.include_router(create_fastapi_router(dsn=DATABASE_DSN, redis_url=REDIS_URL))
# gmail-agent: recent-email sync + auth status under /api/gmail.
app.include_router(create_gmail_router(dsn=DATABASE_DSN, gemini_api_key=GEMINI_API_KEY))

_scraper_lock = asyncio.Lock()
_scraper_state_file = BASE_DIR / ".simplify_scraper_state.json"

_LOG_COLORS = {
    "user_message": "\033[96m",  # bright cyan
    "repository_validation_started": "\033[93m",  # bright yellow
    "repository_validated": "\033[92m",  # bright green
    "tool_call": "\033[95m",  # bright magenta
    "tool_result": "\033[94m",  # bright blue
    "gemini_message": "\033[92m",  # bright green
}
_ANSI_RESET = "\033[0m"


def _write_colored_analyzer_event(event: dict) -> None:
    """Write one live analyzer event to the server console."""

    event_name = str(event.get("event", "event"))
    details = {key: value for key, value in event.items() if key != "event"}
    rendered = json.dumps(details, ensure_ascii=False, default=str)
    if len(rendered) > 8_000:
        rendered = rendered[:8_000] + "… [truncated]"

    timestamp = datetime.now(UTC).strftime("%H:%M:%S")
    prefix = f"[{timestamp}] [repo-analyzer:{event_name}]"
    if sys.stderr.isatty():
        color = _LOG_COLORS.get(event_name, "\033[97m")
        message = f"{color}{prefix}{_ANSI_RESET} {rendered}"
    else:
        message = f"{prefix} {rendered}"
    print(message, file=sys.stderr, flush=True)


def _analyzer(show_logs: bool = False) -> RepositoryAnalyzer:
    """Build an analyzer from the current environment for each request."""

    try:
        callback = _write_colored_analyzer_event if show_logs else None
        return RepositoryAnalyzer(
            Settings.from_env(),
            conversation_callback=callback,
        )
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _raise_analyzer_http_error(exc: Exception) -> None:
    if isinstance(exc, RepositoryReferenceError):
        code = status.HTTP_400_BAD_REQUEST
    elif isinstance(exc, PublicRepositoryRequired):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, ValueError):
        code = status.HTTP_400_BAD_REQUEST
    else:
        code = status.HTTP_502_BAD_GATEWAY
    raise HTTPException(status_code=code, detail=str(exc)) from exc


async def _store_resume_analysis(
    request: ResumeAnalysisRequest,
    analysis: ResumeAnalysis,
) -> None:
    """Persist the resume result as one row in public.projects."""

    connection_string = os.getenv("POSTGRES_URL", "").strip()
    if not connection_string:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="POSTGRES_URL is not configured.",
        )

    repository = RepositoryRef.parse(request.repository)
    try:
        async with await psycopg.AsyncConnection.connect(
            connection_string
        ) as connection:
            await connection.execute(
                """
                INSERT INTO public.projects (
                    user_id,
                    name,
                    description,
                    technologies,
                    architecture
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    request.user_id,
                    repository.name,
                    analysis.Summary,
                    analysis.Technologies,
                    analysis.Architectures,
                ),
            )
    except psycopg.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Resume analysis completed, but the project could not be saved.",
        ) from exc


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Report that the API process is available without calling external services."""

    return HealthResponse()


@app.post(
    "/repo-analyzer/skill",
    response_model=SkillEvaluation,
    tags=["repo-analyzer"],
)
async def evaluate_repository_skill(request: SkillAnalysisRequest) -> SkillEvaluation:
    """Evaluate a public repository against a learning objective."""

    try:
        return await _analyzer(request.show_logs).evaluate_skill(
            request.repository,
            request.learning_objective,
        )
    except HTTPException:
        raise
    except (
        RepositoryAnalysisError,
        RepositoryReferenceError,
        PublicRepositoryRequired,
        APIError,
        MCPError,
        ValueError,
    ) as exc:
        _raise_analyzer_http_error(exc)


@app.post(
    "/repo-analyzer/resume",
    response_model=ResumeAnalysis,
    tags=["repo-analyzer"],
)
async def extract_repository_resume(
    request: ResumeAnalysisRequest,
) -> ResumeAnalysis:
    """Extract evidence-backed resume material from a public repository."""

    try:
        analysis = await _analyzer(request.show_logs).extract_resume_material(
            request.repository
        )
    except HTTPException:
        raise
    except (
        RepositoryAnalysisError,
        RepositoryReferenceError,
        PublicRepositoryRequired,
        APIError,
        MCPError,
        ValueError,
    ) as exc:
        _raise_analyzer_http_error(exc)

    await _store_resume_analysis(request, analysis)
    return analysis


@app.post(
    "/simplify-scraper/jobs",
    response_model=ScrapeJobsResponse,
    tags=["simplify-scraper"],
)
async def scrape_simplify_jobs(request: ScrapeJobsRequest) -> dict:
    """Discover and scrape new Simplify internship postings."""

    if _scraper_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scraper run is already in progress in this API worker.",
        )

    async with _scraper_lock:
        try:
            return await scrape_new_jobs(
                source_url=str(request.source_url),
                state_file=_scraper_state_file if request.use_state else None,
                first_run_max_age_days=request.first_run_max_age_days,
                max_jobs=request.max_jobs,
                concurrency=request.concurrency,
                timeout_seconds=request.timeout_seconds,
                headless=request.headless,
                proxy=request.proxy,
                include_all=request.include_all,
                max_html_chars=request.max_html_chars,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            detail = str(exc)
            unavailable = "not set" in detail or "dependency is missing" in detail
            raise HTTPException(
                status_code=(
                    status.HTTP_503_SERVICE_UNAVAILABLE
                    if unavailable
                    else status.HTTP_502_BAD_GATEWAY
                ),
                detail=detail,
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Scraper failed: {exc}",
            ) from exc
