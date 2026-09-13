"""Run with PYTHONPATH=../backend:../backend/simplify-scraper ../backend/.venv/bin/python -m unittest discover -s tests -p '*_test.py'."""
import os
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
import main
import resume_service as service
import resume_worker as worker
from common import gemini as gemini_helper
from fastapi import HTTPException


def database(rows):
    db = AsyncMock()
    cursors = []
    for row in rows:
        cursor = AsyncMock()
        cursor.fetchone.return_value = row
        cursor.fetchall.return_value = row
        cursors.append(cursor)
    db.execute.side_effect = cursors
    manager = AsyncMock()
    manager.__aenter__.return_value = db
    return db, AsyncMock(return_value=manager)


class ResumeAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_common_gemini_helper_uses_compatibility_schema_field(self):
        ai_client = Mock()
        ai_client.models.generate_content = AsyncMock(
            return_value=Mock(parsed={"suggestions": []})
        )
        schema = {"type": "OBJECT"}

        result = await gemini_helper.generate_json(
            ai_client,
            model="gemini-3.5-flash",
            prompt="test",
            schema=schema,
            system_instruction="test",
            use_response_schema=True,
        )

        config = ai_client.models.generate_content.await_args.kwargs["config"]
        self.assertEqual(result, {"suggestions": []})
        self.assertIs(config["response_schema"], schema)
        self.assertNotIn("response_json_schema", config)

    async def test_service_auth_rejects_missing_or_wrong_token_before_database(self):
        with patch.dict(os.environ, {"RESUME_SERVICE_TOKEN": "test-token"}), patch.object(service, "connect") as connect:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                for headers in ({}, {"X-Resume-Service-Token": "wrong", "X-WorkOS-User": "user_test"}):
                    response = await client.get("/resume-workspace", headers=headers)
                    self.assertEqual(response.status_code, 401)
            connect.assert_not_called()

    async def test_account_mapping_uses_workos_identifier_not_a_browser_uuid(self):
        user_id = uuid4()
        db, connection = database([{"user_id": user_id}])
        with patch.dict(os.environ, {"RESUME_SERVICE_TOKEN": "test-token"}), patch.object(service, "connect", connection):
            self.assertEqual(await service.owner("test-token", "user_example"), user_id)
        self.assertEqual(db.execute.call_args.args[1], ("user_example",))

    async def test_save_conflict_does_not_write_source_or_queue_compilation(self):
        db, connection = database([{}, {"revision": 3}])
        with patch.object(service, "connect", connection), self.assertRaises(HTTPException) as raised:
            await service.save(service.SaveResume(source="tex", filename="resume.tex", revision=2), uuid4())
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(db.execute.call_count, 2)

    async def test_successful_save_queues_exact_revision(self):
        user = uuid4()
        db, connection = database([{}, {"revision": 3}, {}, {}, {"id": uuid4()}])
        with patch.object(service, "connect", connection):
            result = await service.save(service.SaveResume(source="updated", filename="resume.tex", revision=3), user)
        self.assertEqual(result["revision"], 4)
        self.assertEqual(db.execute.call_args.args[1][:3], (user, "compile", 4))

    async def test_project_lookup_is_scoped_to_current_user(self):
        user, project = uuid4(), uuid4()
        db, connection = database([{}, {"revision": 1}, None])
        with patch.object(service, "connect", connection), self.assertRaises(HTTPException) as raised:
            await service.create_operation(service.OperationRequest(kind="generate", revision=1, project_id=project), user)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(db.execute.call_args.args[1], (project, user))

    async def test_workspace_queries_are_all_owner_scoped(self):
        user = uuid4()
        db, connection = database([None, [], []])
        with patch.object(service, "connect", connection):
            await service.workspace(user)
        for call in db.execute.call_args_list:
            self.assertEqual(call.args[1], (user,))
            self.assertIn("WHERE user_id=%s", call.args[0])

    async def test_retry_supersedes_failed_operation_after_replacement_is_queued(self):
        user, failed, replacement = uuid4(), uuid4(), uuid4()
        operation = {
            "id": failed,
            "kind": "scan",
            "revision": None,
            "payload": {"repository": "https://github.com/example/project"},
        }
        lookup_db, lookup_connection = database([operation])
        update_db, update_connection = database([{}])

        with (
            patch.object(
                service,
                "connect",
                side_effect=[lookup_connection(), update_connection()],
            ),
            patch.object(
                service,
                "create_operation",
                AsyncMock(return_value={"id": replacement}),
            ) as create_operation,
        ):
            result = await service.retry(service.RetryRequest(id=failed), user)

        self.assertEqual(result, {"id": replacement})
        create_operation.assert_awaited_once()
        self.assertIn("status='superseded'", update_db.execute.call_args.args[0])
        self.assertEqual(update_db.execute.call_args.args[1], (failed, user))

    async def test_old_compile_is_superseded_without_launching_container(self):
        _, connection = database([None])
        with patch.object(worker, "connect", connection), patch.object(worker, "compile_tex") as compile_tex:
            result = await worker.execute({"kind": "compile", "user_id": uuid4(), "revision": 1})
        self.assertIsNone(result)
        compile_tex.assert_not_called()

    async def test_compile_publish_is_guarded_by_source_revision(self):
        user = uuid4()
        db, connection = database([{"source": "test"}, {}])
        with patch.object(worker, "connect", connection), patch.object(worker, "compile_tex", return_value=b"%PDF-test"):
            await worker.execute({"kind": "compile", "user_id": user, "revision": 3})
        self.assertIn("WHERE user_id=%s AND revision=%s", db.execute.call_args.args[0])
        self.assertEqual(db.execute.call_args.args[1][-2:], (user, 3))

    async def test_review_uses_gemini_compatible_response_schema(self):
        result = {
            "suggestions": [{
                "id": "1",
                "original": "Built an API",
                "text": "Implemented an API",
                "feedback": [],
            }],
        }
        client = Mock()
        client.aio = Mock()
        client.aio.aclose = AsyncMock()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
            patch.object(worker.genai, "Client", return_value=client),
            patch.object(worker, "generate_json", AsyncMock(return_value=result)) as generate_json,
        ):
            reviewed = await worker.suggest({
                "kind": "review",
                "payload": {"bullets": [{"id": "1", "text": "Built an API"}]},
                "user_id": uuid4(),
            })

        self.assertEqual(reviewed["suggestions"][0]["text"], "Implemented an API")
        self.assertTrue(generate_json.await_args.kwargs["use_response_schema"])
        self.assertIs(generate_json.await_args.kwargs["schema"], worker.SUGGESTIONS_RESPONSE_SCHEMA)
        self.assertIn(
            "Never put a period at the end",
            generate_json.await_args.kwargs["system_instruction"],
        )
        client.aio.aclose.assert_awaited_once()


class CompilerAndReviewTests(unittest.TestCase):
    def test_resume_generation_defaults_to_gemini_3_5_flash(self):
        self.assertEqual(worker.DEFAULT_RESUME_GEMINI_MODEL, "gemini-3.5-flash")

    def test_resume_response_schema_avoids_unsupported_json_schema_keywords(self):
        serialized = str(worker.SUGGESTIONS_RESPONSE_SCHEMA)
        for keyword in ("additionalProperties", "$defs", "$ref", "maxLength", "maxItems"):
            self.assertNotIn(keyword, serialized)

    def test_compile_has_no_host_mounts_network_or_shell_escape(self):
        with patch.object(subprocess, "run", return_value=Mock(returncode=0, stdout=b"%PDF-test", stderr=b"")) as run:
            self.assertEqual(worker.compile_tex("hello"), b"%PDF-test")
        command = run.call_args_list[0].args[0]
        for flag in (
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--user=65534:65534",
            "--memory=256m",
            "--tmpfs=/work:rw,noexec,nosuid,size=32m,mode=1777",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=32m,mode=1777",
            "--env=HOME=/work",
            "--env=TMPDIR=/tmp",
            "--env=TEXMFVAR=/work/texmf-var",
            "--env=TEXMFCONFIG=/work/texmf-config",
            "--env=TEXMFCACHE=/work/texmf-cache",
        ):
            self.assertIn(flag, command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("-v", command)
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 45)

    def test_timeout_cleans_up_container_and_reports_retryable_failure(self):
        with patch.object(subprocess, "run", side_effect=[subprocess.TimeoutExpired("docker", 45), Mock()]) as run:
            with self.assertRaisesRegex(RuntimeError, "45 seconds"):
                worker.compile_tex("bad")
        self.assertEqual(run.call_args_list[1].args[0][:3], ["docker", "rm", "-f"])

    def test_compile_rejects_non_pdf_and_oversized_output(self):
        for output in (b"not-pdf", b"%PDF-" + b"a" * (2 * 1024 * 1024)):
            with patch.object(subprocess, "run", return_value=Mock(returncode=0, stdout=output, stderr=b"")):
                with self.assertRaises(RuntimeError):
                    worker.compile_tex("input")

    def test_review_rejects_invented_numbers_and_mismatched_ids(self):
        payload = {"bullets": [{"id": "1", "text": "Built an API"}]}
        result = {"suggestions": [{"id": "1", "original": "Built an API", "text": "Built an API serving 1000 users", "feedback": []}]}
        with self.assertRaisesRegex(ValueError, "unsupported number"):
            worker.validate_suggestions(result, payload, {})
        result["suggestions"][0]["text"] = "Implemented an API"
        result["suggestions"][0]["id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "mismatched"):
            worker.validate_suggestions(result, payload, {})

    def test_review_uses_original_from_request_and_allows_supported_metric(self):
        payload = {"bullets": [{"id": "1", "text": "Built an API for 100 users"}]}
        result = {"suggestions": [{"id": "1", "original": "model changed original", "text": "Served 100 users through an API", "feedback": []}]}
        validated = worker.validate_suggestions(result, payload, {})
        self.assertEqual(validated["suggestions"][0]["original"], payload["bullets"][0]["text"])

    def test_upload_validation(self):
        for filename, source in (("resume.pdf", "text"), ("../resume.tex", "text"), ("resume.tex", "\x00"), ("resume.tex", "é" * 600000)):
            with self.assertRaises(ValueError):
                service.SaveResume(source=source, filename=filename, revision=0)
