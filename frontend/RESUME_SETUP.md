# Resume workspace

The `/resume` page uses WorkOS authentication, a dedicated `app_users` identity mapping, PostgreSQL persistence, and the Python backend. It has no economy or reward dependencies. One active single-file `.tex` resume is stored per account.

## Setup

1. Set `POSTGRES_URL` in frontend `.env` and backend `.env` to the same database. Set frontend `RESUME_BACKEND_URL` to the Python API address (default development port: 8000). Generate a service token with `openssl rand -hex 32` and set the same `RESUME_SERVICE_TOKEN` in both environments. These values must remain server-only.
2. From `frontend/`, run `node scripts/migrate-resume.mjs --check`, then `node scripts/migrate-resume.mjs --apply`. The migration targets the current UUID-based `public.projects` schema (`project_id`, `architecture`). It preserves existing UUID ownership in unmapped `app_users` records, removes only the projects-to-economy foreign key, and adds the new account foreign key. It never links legacy records to WorkOS by guessing an email or user identity. Backfill `workos_user_id` only after verifying the owner.
3. From `backend/`, build the compiler: `docker build -t upjob-tex:local resume-compiler`. A running Docker daemon is required on the worker host. The image includes the LaTeX packages used by Jake's Resume. Set `RESUME_TEX_IMAGE` to use another prebuilt compatible image. Uploaded source is never compiled directly on the application host.
4. Start the API with `.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000` and a separate worker with `.venv/bin/python resume_worker.py`. Run additional worker processes if needed; PostgreSQL `SKIP LOCKED` prevents duplicate claims. Use a process supervisor in deployment. Queued work survives restarts; interrupted running work becomes retryable after 15 minutes. Jobs have a ten-minute execution deadline.
5. Keep `GEMINI_API_KEY` and `GITHUB_PAT` (or `GITHUB_TOKEN`) configured in the backend for repo scans. Resume review and regeneration default to `gemini-3.5-flash`; set `RESUME_GEMINI_MODEL` to override it without changing the repository analyzer model. Start the frontend with `bun run dev` and open `/resume` after signing in.

## Contracts and behavior

The browser calls `/api/resume`; Next.js validates the WorkOS session and mutation origin, limits request size, and forwards only the authenticated user ID and service credential. Python routes under `/resume-workspace` provide GET/PUT workspace, GET PDF, POST operations, and POST retry. The Python API must be private to the frontend service; existing non-workspace endpoints retain their previous authentication behavior.

Saving requires the last known revision and atomically queues compilation. Conflict responses preserve local edits; users can download them before reloading. PDF publishing checks the revision again, so slow compiles cannot overwrite newer output. Failed compilation leaves the prior successful PDF intact. PDFs are capped at 2 MB and TeX sources at 1 MB. Additional source files and custom fonts are not accepted.

The starter follows Jake's command conventions. Bullet parsing supports `resumeItem` and `resumeSubItem` within `resumeItemListStart/End`, with balanced braces, escaped characters, and comments. Manual source editing is available for unsupported constructs. The bullet editor represents bold text as `**bold text**` and safely writes it as `\\textbf{bold text}`; other inline formatting becomes plain text when that bullet is saved. The rest of the document stays intact. Project insertion requires a Jake-style Projects section. New entries contain an explicit placeholder bullet to replace with reviewed content.

Review and generation use existing facts plus optional selected project/context. Generation includes a second review pass. Numerical claims absent from the supplied evidence are rejected. Suggestions are tied to source revisions: accept all desired suggestions together, or request a fresh review after an edit. Select the destination entry before inserting project suggestions. Dismissal and entry selection are browser-session UI state; analysis and suggestions persist in PostgreSQL.

## Verification

From `frontend/`:

```sh
bun test tests/resume-latex.test.ts
bun run lint
bunx tsc --noEmit --incremental false
bun run build
PYTHONPATH=../backend:../backend/simplify-scraper ../backend/.venv/bin/python -m unittest discover -s tests -p '*_test.py' -v
PYTHONPATH=../backend:../backend/simplify-scraper ../backend/.venv/bin/python -m unittest discover -s ../backend/tests -v
../backend/.venv/bin/python scripts/check-resume-integration.py
```

Manual acceptance: upload a Jake-style resume, edit a bullet, confirm PDF revision advances, introduce a LaTeX error and confirm the prior PDF stays visible, repair it, scan a public repository, review and accept generated bullets, reload to verify persistence, and verify two signed-in accounts cannot access each other's workspace. Inspect the stacked layout on mobile. No AI calls are required by the automated tests.

The integration script uses the configured database, creates synthetic users within a transaction, verifies storage and ownership, and rolls back all its test data. If Turbopack cannot bind its build-time port in a restricted environment, `bunx next build --webpack` provides an alternative production build check.
