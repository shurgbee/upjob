"""Exercise real storage with synthetic users; always roll back all test data."""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from dotenv import load_dotenv

frontend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(frontend.parent / "backend"))
load_dotenv(frontend / ".env", override=True)
import main
import resume_service as service
import resume_worker as worker
from fastapi import HTTPException
from repo_analyzer.models import ResumeAnalysis


async def check():
    async with await service.connect() as db:
        async with db.transaction(force_rollback=True):
            @asynccontextmanager
            async def borrowed():
                yield db

            connection = AsyncMock(side_effect=lambda: borrowed())
            with patch.object(service, "connect", connection), patch.object(worker, "connect", connection), patch.dict(os.environ, {"RESUME_SERVICE_TOKEN": "integration-only"}):
                workos_id = f"user_resume_test_{uuid4().hex}"
                first = await service.owner("integration-only", workos_id)
                assert first == await service.owner("integration-only", workos_id)
                other = await service.owner("integration-only", f"user_resume_test_{uuid4().hex}")
                saved = await service.save(service.SaveResume(source="synthetic source", filename="test.tex", revision=0), first)
                assert saved["revision"] == 1
                assert (await service.workspace(other))["resume"] is None
                try:
                    await service.save(service.SaveResume(source="stale", filename="test.tex", revision=0), first)
                    raise AssertionError("Stale save accepted")
                except HTTPException as exc:
                    assert exc.status_code == 409
                with patch.object(worker, "compile_tex", return_value=b"%PDF-synthetic"):
                    await worker.execute({"kind": "compile", "user_id": first, "revision": 1})
                assert (await service.pdf(first)).body == b"%PDF-synthetic"
                await service.save(service.SaveResume(source="revision two", filename="test.tex", revision=1), first)
                assert (await service.workspace(first))["resume"]["pdf_revision"] == 1
                assert await worker.execute({"kind": "compile", "user_id": first, "revision": 1}) is None
                analysis = ResumeAnalysis(Summary="Synthetic project", Architectures=["REST API"], Technologies=["Python"], Actions=["Built API"], Metrics=[])
                scan_job = {"kind": "scan", "user_id": first, "payload": {"repository": "https://github.com/upjob-test/synthetic", "context": "Test-only evidence"}}
                with patch.object(main, "_analyzer", return_value=Mock(extract_resume_material=AsyncMock(return_value=analysis))):
                    project = await worker.execute(scan_job)
                    assert project == await worker.execute(scan_job)
                assert len((await service.workspace(first))["projects"]) == 1
                assert (await service.workspace(other))["projects"] == []
                try:
                    await service.create_operation(service.OperationRequest(kind="scan", repository="https://github.com/upjob-test/synthetic", project_id=project["project_id"]), other)
                    raise AssertionError("Cross-account project accepted")
                except HTTPException as exc:
                    assert exc.status_code == 404
                print("Passed: identity mapping, persistence, revision conflicts, PDF preservation, stale compilation, project upsert, account isolation. All synthetic records rolled back.")


if __name__ == "__main__":
    asyncio.run(check())
