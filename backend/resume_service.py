"""Authenticated resume API. Mounted by main.py; jobs run in resume_worker.py."""
from __future__ import annotations

import hmac
import os
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator


def connect():
    dsn = os.getenv("POSTGRES_URL")
    if not dsn:
        raise HTTPException(503, "POSTGRES_URL is not configured.")
    return psycopg.AsyncConnection.connect(dsn, row_factory=dict_row, connect_timeout=10)


async def owner(
    x_resume_service_token: Annotated[str, Header()] = "",
    x_workos_user: Annotated[str, Header()] = "",
) -> UUID:
    token = os.getenv("RESUME_SERVICE_TOKEN", "")
    if not token:
        raise HTTPException(503, "RESUME_SERVICE_TOKEN is not configured.")
    if not hmac.compare_digest(token, x_resume_service_token):
        raise HTTPException(401, "Invalid service credential.")
    if not x_workos_user.startswith("user_") or len(x_workos_user) > 200:
        raise HTTPException(401, "A WorkOS user is required.")
    async with await connect() as db:
        row = await (await db.execute(
            "INSERT INTO app_users(workos_user_id) VALUES (%s) ON CONFLICT(workos_user_id) "
            "DO UPDATE SET workos_user_id=EXCLUDED.workos_user_id RETURNING user_id", (x_workos_user,)
        )).fetchone()
        return row["user_id"]


Owner = Annotated[UUID, Depends(owner)]
router = APIRouter(prefix="/resume-workspace", tags=["resume"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaveResume(StrictModel):
    source: str = Field(min_length=1, max_length=1024 * 1024)
    filename: str = Field(min_length=5, max_length=200)
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_tex(self):
        if len(self.source.encode("utf-8")) > 1024 * 1024 or "\x00" in self.source:
            raise ValueError("Upload must be a UTF-8 text file up to 1 MB.")
        if not self.filename.lower().endswith(".tex") or "/" in self.filename or "\\" in self.filename:
            raise ValueError("Use a .tex filename without directories.")
        return self


class Bullet(StrictModel):
    id: str = Field(max_length=40)
    text: str = Field(min_length=1, max_length=10000)


class OperationRequest(StrictModel):
    kind: Literal["compile", "scan", "review", "generate"]
    revision: int | None = Field(default=None, ge=1)
    repository: str = Field(default="", max_length=500)
    context: str = Field(default="", max_length=10000)
    project_id: UUID | None = None
    bullets: list[Bullet] = Field(default_factory=list, max_length=100)


class RetryRequest(StrictModel):
    id: UUID


async def enqueue(db, user_id, kind, revision, payload):
    return await (await db.execute(
        "INSERT INTO resume_operations(user_id,kind,revision,payload) VALUES (%s,%s,%s,%s) RETURNING id",
        (user_id, kind, revision, Jsonb(payload)),
    )).fetchone()


@router.get("")
async def workspace(user_id: Owner):
    async with await connect() as db:
        resume = await (await db.execute(
            "SELECT filename,source,revision,pdf_revision,compile_status,compile_error FROM resumes WHERE user_id=%s", (user_id,)
        )).fetchone()
        projects = await (await db.execute(
            "SELECT project_id,name,description,technologies,architecture,github_repo_url,analysis,user_context "
            "FROM projects WHERE user_id=%s ORDER BY spec_updated_at DESC", (user_id,)
        )).fetchall()
        operations = await (await db.execute(
            "SELECT id,kind,status,revision,payload,result,error,created_at FROM resume_operations "
            "WHERE user_id=%s ORDER BY created_at DESC LIMIT 30", (user_id,)
        )).fetchall()
    return {"resume": resume, "projects": projects, "operations": operations}


@router.put("")
async def save(request: SaveResume, user_id: Owner):
    async with await connect() as db:
        # Serialize create + update, including the initial revision-zero save.
        await db.execute("SELECT user_id FROM app_users WHERE user_id=%s FOR UPDATE", (user_id,))
        current = await (await db.execute("SELECT revision FROM resumes WHERE user_id=%s", (user_id,))).fetchone()
        if (current["revision"] if current else 0) != request.revision:
            raise HTTPException(409, "Resume changed in another tab. Download your local LaTeX, then reload to reconcile changes.")
        revision = request.revision + 1
        await db.execute(
            "INSERT INTO resumes(user_id,filename,source,revision) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT(user_id) DO UPDATE SET filename=EXCLUDED.filename,source=EXCLUDED.source,"
            "revision=EXCLUDED.revision,compile_status='queued',compile_error=NULL,updated_at=now()",
            (user_id, request.filename, request.source, revision),
        )
        await db.execute("UPDATE resume_operations SET status='superseded',finished_at=now() WHERE user_id=%s AND kind='compile' AND status='queued'", (user_id,))
        operation = await enqueue(db, user_id, "compile", revision, {})
    return {"revision": revision, "operation_id": operation["id"]}


@router.get("/pdf")
async def pdf(user_id: Owner):
    async with await connect() as db:
        row = await (await db.execute("SELECT pdf FROM resumes WHERE user_id=%s", (user_id,))).fetchone()
    if not row or not row["pdf"]:
        raise HTTPException(404, "No compiled PDF yet.")
    return Response(bytes(row["pdf"]), media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


@router.post("/operations", status_code=202)
async def create_operation(request: OperationRequest, user_id: Owner):
    from repo_analyzer.repository import RepositoryRef, RepositoryReferenceError
    payload = request.model_dump(mode="json", exclude={"kind", "revision"})
    if request.kind == "scan":
        try:
            repo = RepositoryRef.parse(request.repository)
            payload["repository"] = f"https://github.com/{repo.full_name.lower()}"
        except RepositoryReferenceError as exc:
            raise HTTPException(400, str(exc)) from exc
    if request.kind == "review" and not request.bullets:
        raise HTTPException(400, "Select at least one bullet.")
    if request.kind == "generate" and not request.project_id and not request.bullets:
        raise HTTPException(400, "Select a project or bullet to regenerate.")
    async with await connect() as db:
        await db.execute("SELECT user_id FROM app_users WHERE user_id=%s FOR UPDATE", (user_id,))
        if request.kind != "scan":
            row = await (await db.execute("SELECT revision FROM resumes WHERE user_id=%s", (user_id,))).fetchone()
            if not row or row["revision"] != request.revision:
                raise HTTPException(409, "Save the current resume before requesting this operation.")
        if request.project_id:
            project = await (await db.execute("SELECT project_id FROM projects WHERE project_id=%s AND user_id=%s", (request.project_id, user_id))).fetchone()
            if not project:
                raise HTTPException(404, "Project not found.")
        active = await (await db.execute("SELECT count(*) AS n FROM resume_operations WHERE user_id=%s AND status IN ('queued','running')", (user_id,))).fetchone()
        if active["n"] >= 5:
            raise HTTPException(429, "Wait for current operations to finish before starting another.")
        result = await enqueue(db, user_id, request.kind, request.revision, payload)
        if request.kind == "compile":
            await db.execute("UPDATE resumes SET compile_status='queued',compile_error=NULL WHERE user_id=%s", (user_id,))
    return result


@router.post("/retry", status_code=202)
async def retry(request: RetryRequest, user_id: Owner):
    async with await connect() as db:
        row = await (await db.execute("SELECT * FROM resume_operations WHERE id=%s AND user_id=%s AND status='failed'", (request.id, user_id))).fetchone()
    if not row:
        raise HTTPException(404, "Failed operation not found.")
    return await create_operation(OperationRequest(kind=row["kind"], revision=row["revision"], **row["payload"]), user_id)
