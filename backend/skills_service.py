"""Authenticated skill-gap and build-quest API."""
from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from repo_analyzer.repository import RepositoryRef, RepositoryReferenceError
from resume_service import Owner, connect


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationRequest(StrictModel):
    repository: str = Field(min_length=3, max_length=500)


class RetryRequest(StrictModel):
    id: UUID


router = APIRouter(prefix="/skills-workspace", tags=["skills"])


def normalize_competency(value: str) -> str:
    return " ".join(value.split()).casefold()


def build_skill_gaps(
    jobs: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    passed_quests: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Count each competency at most once per job and remove demonstrated skills."""
    known: set[str] = set()
    for project in projects:
        for value in (*project.get("technologies", []), *project.get("architecture", [])):
            key = normalize_competency(str(value))
            if key:
                known.add(key)
    for quest in passed_quests:
        for value in quest.get("target_skills", []):
            key = normalize_competency(str(value))
            if key:
                known.add(key)

    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for job in jobs:
        unique: dict[str, str] = {}
        for value in (*job.get("technologies", []), *job.get("architecture", [])):
            label = " ".join(str(value).split())
            key = normalize_competency(label)
            if key and key not in unique:
                unique[key] = label
        for key, label in unique.items():
            counts[key] += 1
            labels.setdefault(key, label)

    missing = [
        {"name": labels[key], "job_count": count}
        for key, count in counts.items()
        if key not in known
    ]
    missing.sort(key=lambda item: (-item["job_count"], item["name"].casefold()))
    return missing, len(known)


async def _gap_snapshot(db, user_id: UUID) -> tuple[list[dict[str, Any]], int, int]:
    jobs = await (await db.execute(
        "SELECT technologies, architecture FROM job_specs"
    )).fetchall()
    projects = await (await db.execute(
        "SELECT technologies, architecture FROM projects WHERE user_id=%s",
        (user_id,),
    )).fetchall()
    passed = await (await db.execute(
        "SELECT target_skills FROM skill_quests WHERE user_id=%s AND status='passed'",
        (user_id,),
    )).fetchall()
    gaps, known_count = build_skill_gaps(jobs, projects, passed)
    return gaps, known_count, len(jobs)


async def _enqueue(db, user_id: UUID, kind: str, payload: dict, quest_id=None):
    return await (await db.execute(
        "INSERT INTO skill_operations(user_id,quest_id,kind,payload) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (user_id, quest_id, kind, Jsonb(payload)),
    )).fetchone()


@router.get("")
async def workspace(user_id: Owner):
    async with await connect() as db:
        gaps, known_count, job_count = await _gap_snapshot(db, user_id)
        quests = await (await db.execute(
            "SELECT quest_id,title,description,target_skills,learning_objective,"
            "acceptance_criteria,status,repository_url,evaluation,created_at,updated_at "
            "FROM skill_quests WHERE user_id=%s ORDER BY created_at,quest_id",
            (user_id,),
        )).fetchall()
        operations = await (await db.execute(
            "SELECT id,quest_id,kind,status,payload,result,error,created_at "
            "FROM skill_operations WHERE user_id=%s ORDER BY created_at DESC LIMIT 30",
            (user_id,),
        )).fetchall()
    return {
        "job_count": job_count,
        "known_skill_count": known_count,
        "missing_skills": gaps,
        "quests": quests,
        "operations": operations,
    }


@router.post("/plan", status_code=202)
async def create_plan(user_id: Owner):
    async with await connect() as db:
        await db.execute("SELECT user_id FROM app_users WHERE user_id=%s FOR UPDATE", (user_id,))
        existing = await (await db.execute(
            "SELECT quest_id FROM skill_quests WHERE user_id=%s LIMIT 1", (user_id,)
        )).fetchone()
        if existing:
            raise HTTPException(409, "Your skill projects have already been created.")
        active = await (await db.execute(
            "SELECT id FROM skill_operations WHERE user_id=%s AND kind='plan' "
            "AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )).fetchone()
        if active:
            return active
        gaps, _, job_count = await _gap_snapshot(db, user_id)
        if not gaps:
            raise HTTPException(409, "No missing skills are available to build a plan.")
        operation = await _enqueue(
            db,
            user_id,
            "plan",
            {"job_count": job_count, "missing_skills": gaps[:12]},
        )
    return operation


@router.post("/quests/{quest_id}/evaluate", status_code=202)
async def evaluate_quest(quest_id: UUID, request: EvaluationRequest, user_id: Owner):
    try:
        repo = RepositoryRef.parse(request.repository)
    except RepositoryReferenceError as exc:
        raise HTTPException(400, str(exc)) from exc
    repository = f"https://github.com/{repo.full_name.lower()}"

    async with await connect() as db:
        await db.execute("SELECT user_id FROM app_users WHERE user_id=%s FOR UPDATE", (user_id,))
        quest = await (await db.execute(
            "SELECT quest_id FROM skill_quests WHERE quest_id=%s AND user_id=%s",
            (quest_id, user_id),
        )).fetchone()
        if not quest:
            raise HTTPException(404, "Skill project not found.")
        active = await (await db.execute(
            "SELECT id FROM skill_operations WHERE quest_id=%s AND kind='evaluate' "
            "AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
            (quest_id,),
        )).fetchone()
        if active:
            return active
        total = await (await db.execute(
            "SELECT count(*) AS n FROM skill_operations WHERE user_id=%s "
            "AND status IN ('queued','running')", (user_id,)
        )).fetchone()
        if total["n"] >= 5:
            raise HTTPException(429, "Wait for current skill operations to finish.")
        operation = await _enqueue(
            db, user_id, "evaluate", {"repository": repository}, quest_id
        )
        await db.execute(
            "UPDATE skill_quests SET status='evaluating',repository_url=%s,updated_at=now() "
            "WHERE quest_id=%s", (repository, quest_id)
        )
    return operation


@router.post("/retry", status_code=202)
async def retry_operation(request: RetryRequest, user_id: Owner):
    async with await connect() as db:
        await db.execute("SELECT user_id FROM app_users WHERE user_id=%s FOR UPDATE", (user_id,))
        failed = await (await db.execute(
            "SELECT * FROM skill_operations WHERE id=%s AND user_id=%s AND status='failed'",
            (request.id, user_id),
        )).fetchone()
        if not failed:
            raise HTTPException(404, "Failed skill operation not found.")
        active = await (await db.execute(
            "SELECT id FROM skill_operations WHERE user_id=%s AND kind=%s "
            "AND (%s::uuid IS NULL OR quest_id=%s) AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, failed["kind"], failed["quest_id"], failed["quest_id"]),
        )).fetchone()
        if active:
            return active
        operation = await _enqueue(
            db, user_id, failed["kind"], failed["payload"], failed["quest_id"]
        )
        if failed["kind"] == "evaluate":
            await db.execute(
                "UPDATE skill_quests SET status='evaluating',updated_at=now() WHERE quest_id=%s",
                (failed["quest_id"],),
            )
    return operation
