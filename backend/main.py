"""HTTP API for the repository analyzer and Simplify job scraper.

Run from this directory with::

    uvicorn main:app --reload

The two underlying projects are kept in their existing directories.  The path
setup below lets this module run directly from a source checkout without first
installing either project as a package.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
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
RESUME_TAILOR_DIR = BASE_DIR / "resume-tailor"

# These projects intentionally remain independently runnable.  Add their source
# roots when this API is launched from the repository root.  BASE_DIR is added so
# the shared ``common`` package (imported by resume-tailor) resolves as well.
for source_root in (
    REPO_ANALYZER_DIR / "src",
    SIMPLIFY_SCRAPER_DIR,
    RESUME_TAILOR_DIR,
    BASE_DIR,
):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

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
from schemas import build_job_specification
from retrieval import select_projects
from resume_tailor import tailor_resume
from common.db import connect as connect_asyncpg

DEFAULT_REPOSITORY = "PyroSh0ck/miniProjects-langGraph"

# Load local development configuration without replacing deployment-provided
# environment variables.
for dotenv_path in (
    BASE_DIR / ".env",
    REPO_ANALYZER_DIR / ".env",
    SIMPLIFY_SCRAPER_DIR / ".env",
    RESUME_TAILOR_DIR / ".env",
):
    load_dotenv(dotenv_path=dotenv_path, override=False)


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
        description="User UUID from public.user_economy that owns the saved project.",
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


class TailorResumeRequest(APIModel):
    job_id: int = Field(
        ge=1,
        examples=[1],
        description="Identity (job_specs.id) of the target posting to tailor against.",
    )
    user_id: UUID = Field(
        description="User UUID; scopes which of the user's analyzed projects are retrieved.",
    )
    candidate_name: str = Field(
        default="Candidate",
        min_length=1,
        description="Name rendered in the resume header.",
    )
    top_k: Annotated[int, Field(ge=1, le=10)] = 4
    threshold: Annotated[float, Field(ge=0, le=1)] = 0.8


class MatchProjectsRequest(APIModel):
    job_id: int = Field(
        ge=1,
        examples=[1],
        description="Identity (job_specs.id) of the target posting to match projects against.",
    )
    user_id: UUID = Field(
        description="User UUID; scopes which of the user's analyzed projects are ranked.",
    )
    top_k: Annotated[int, Field(ge=1, le=10)] = 4
    threshold: Annotated[float, Field(ge=0, le=1)] = 0.8


class TailorResumeResponse(APIModel):
    status: Literal["ok", "empty", "error"]
    error: str | None = None
    job_id: int
    job_title: str
    candidate_name: str
    user_id: str | None = None
    selected_projects: list[dict]
    tex_chars: int
    latex: str = Field(description="The generated LaTeX resume source.")


class MatchProjectsResponse(APIModel):
    job_id: int
    job_title: str
    count: int
    projects: list[dict]


class HealthResponse(APIModel):
    status: Literal["ok"] = "ok"
    services: tuple[str, str, str] = (
        "repo-analyzer",
        "simplify-scraper",
        "resume-tailor",
    )


app = FastAPI(
    title="Upjob Backend API",
    version="1.0.0",
    description="HTTP interface for repo-analyzer and simplify-scraper.",
)

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


def _resume_tailor_dsn() -> str:
    """Resolve the PostgreSQL DSN shared with repo-analyzer/resume-tailor."""

    dsn = os.getenv("DATABASE_URL", "").strip() or os.getenv("POSTGRES_URL", "").strip()
    if not dsn:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL is not configured.",
        )
    return dsn


def _gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY is not configured.",
        )
    return key


_JOB_SPEC_SQL = """
    SELECT title, url, company, category, employment_type, description,
           requirements, technologies, architecture, yoe, publish_date
    FROM public.job_specs
    WHERE id = $1
"""


async def _fetch_job_spec(conn, job_id: int) -> dict | None:
    """Load one job_specs row and shape it into a JobSpecification payload."""

    row = await conn.fetchrow(_JOB_SPEC_SQL, job_id)
    if row is None:
        return None
    publish_date = row["publish_date"]
    # ``requirements`` is TEXT[] in the source schema but plain TEXT in some
    # deployed databases; accept either shape.
    requirements = row["requirements"]
    if isinstance(requirements, str):
        requirements = [requirements] if requirements else []
    else:
        requirements = list(requirements or [])
    return {
        "title": row["title"],
        "url": row["url"] or "",
        "company": row["company"],
        "category": row["category"],
        "employment_type": row["employment_type"],
        "description": row["description"],
        "requirements": requirements,
        "technologies": list(row["technologies"] or []),
        "architecture": list(row["architecture"] or []),
        "yoe": row["yoe"],
        "publish_date": publish_date.isoformat() if publish_date is not None else None,
    }


@app.post(
    "/resume-tailor/tailor",
    response_model=TailorResumeResponse,
    tags=["resume-tailor"],
)
async def tailor_resume_endpoint(request: TailorResumeRequest) -> TailorResumeResponse:
    """Build a tailored LaTeX resume for a stored job posting and user."""

    dsn = _resume_tailor_dsn()
    _gemini_api_key()  # Fail fast with 503 before doing any work.

    conn = await connect_asyncpg(dsn)
    try:
        job_spec = await _fetch_job_spec(conn, request.job_id)
    finally:
        await conn.close()

    if job_spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No job_specs row with id={request.job_id}.",
        )

    handle, tmp_path = tempfile.mkstemp(suffix=".tex")
    os.close(handle)
    try:
        envelope = await tailor_resume(
            job_spec,
            str(request.user_id),
            candidate_name=request.candidate_name,
            output_path=tmp_path,
            database_url=dsn,
            top_k=request.top_k,
            threshold=request.threshold,
        )
        try:
            latex = Path(tmp_path).read_text(encoding="utf-8")
        except OSError:
            latex = ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # tailor_resume degrades rather than aborts, so a failed pipeline returns a
    # 200 envelope with status "error"/"empty" (configuration gaps already 503'd).
    return TailorResumeResponse(
        status=envelope.get("status", "error"),
        error=envelope.get("error"),
        job_id=request.job_id,
        job_title=envelope.get("job_title") or job_spec["title"],
        candidate_name=envelope.get("candidate_name", request.candidate_name),
        user_id=envelope.get("user_id"),
        selected_projects=envelope.get("selected_projects", []),
        tex_chars=envelope.get("tex_chars", len(latex)),
        latex=latex,
    )


@app.post(
    "/resume-tailor/match-projects",
    response_model=MatchProjectsResponse,
    tags=["resume-tailor"],
)
async def match_projects_endpoint(
    request: MatchProjectsRequest,
) -> MatchProjectsResponse:
    """Rank a user's projects against a stored job posting without generating bullets."""

    dsn = _resume_tailor_dsn()
    api_key = _gemini_api_key()

    conn = await connect_asyncpg(dsn)
    try:
        job_spec = await _fetch_job_spec(conn, request.job_id)
        if job_spec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No job_specs row with id={request.job_id}.",
            )
        job = build_job_specification(job_spec)
        try:
            projects = await select_projects(
                conn,
                job,
                str(request.user_id),
                api_key=api_key,
                threshold=request.threshold,
                top_k=request.top_k,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except APIError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Embedding failed: {exc}",
            ) from exc
    finally:
        await conn.close()

    return MatchProjectsResponse(
        job_id=request.job_id,
        job_title=job.title,
        count=len(projects),
        projects=[project.to_dict() for project in projects],
    )
