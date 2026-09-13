# Documentation — gmail-agent

How the feature works, how to navigate the code, and which file to open for a
given bug. For setup and commands see `README.md`; for status and the TODO list
see `../PROGRESS.md` (backend-wide).

## What it does

Reconciles recent Gmail job-application *completion* emails (automated
confirmations that an application was submitted) to the `job_applications`
table: attaching the email's thread to a matching open row, or inserting a new
completed row when no open row plausibly matches. Runs on a 2-hour schedule in
the umbrella app and on-demand via `POST /api/gmail/sync`. Idempotent — a
thread already linked to a row is skipped on every subsequent run.

## File map

| File | Responsibility |
|---|---|
| `gmail_agent.py` | Public async entry point `sync_recent_threads` + `authorize` + FastAPI router (`create_fastapi_router`) + CLI. |
| `gmail_mcp_client.py` | Gmail transport: `search_recent_threads`, `auth_status`. MCP (official Gmail MCP server) or REST (Gmail API + OAuth) via `GMAIL_TRANSPORT`. |
| `gmail_classifier.py` | `classify_threads` — one Gemini call to keep only completion emails and extract company/role/status. |
| `gmail_matcher.py` | `match_emails` — one Gemini call mapping each qualifying email to an open row's `ctid`, or `None`. |
| `gmail_persistence.py` | `ensure_schema`, `resolve_default_user_id`, `thread_exists`, `fetch_open_candidates`, `attach_thread_to_row`, `insert_new_row`, plus pure helpers `_load_json`/`_first_str`. |
| `gmail_schemas.py` | Prompts (`build_classify_prompt`, `build_match_prompt`) and JSON schemas (`CLASSIFY_SCHEMA`, `MATCH_SCHEMA`) for the two Gemini calls. Pure, stdlib-only. |
| `gmail_scheduler.py` | `start_gmail_scheduler` — 2-hour `AsyncIOScheduler` job calling `sync_recent_threads`, started from the umbrella app's lifespan. |
| `migrations/003_job_applications_thread_id_text.sql` | Retypes the live `job_applications.thread_id` column from `uuid` to `text` and adds a partial unique index. |
| `tests/` | Offline stdlib `unittest` — one module per source file plus the orchestration test. |

Modules other than `gmail_agent.py` are prefixed `gmail_` (rather than bare
`schemas`/`persistence`/... names) to avoid a `sys.path` collision with
same-named modules in `reward-handler` when both components are imported into
the umbrella app's single process.

## The 5-stage pipeline

`sync_recent_threads(hours=2, user_id=None, *, dsn=None, gemini_api_key=None, model=...)`
in `gmail_agent.py`:

```
search_recent_threads(hours)                          [gmail_mcp_client.py]
  │  Gmail "newer:<epoch>" query -> [{thread_id, subject, sender, snippet}]
  ▼
classify_threads(ai_client, threads)                   [gmail_classifier.py]
  │  one Gemini call; keep only is_completion entries, enrich with
  │  company/role/status; empty input short-circuits, no API call
  │  --- if no qualifying emails, return early: no DB connection opened ---
  ▼
for each qualifying email:
  thread_exists(conn, thread_id)?                      [gmail_persistence.py]
  │  yes -> skipped_existing += 1, continue (idempotency guard)
  ▼
match_emails(ai_client, qualifying, candidates)         [gmail_matcher.py]
  │  one Gemini call over open rows (thread_id IS NULL); email -> ctid | None
  ▼
attach_thread_to_row(conn, ctid, thread_id)             [gmail_persistence.py]
  │  UPDATE ... WHERE ctid = $1 AND thread_id IS NULL
  │  True (row claimed)  -> matched += 1
  │  False (already taken, or no ctid decided) -> fall back:
  ▼
insert_new_row(conn, user_id, metadata, thread_id)      [gmail_persistence.py]
     created += 1
```

All DB work (`fetch_open_candidates`, the per-email attach/insert loop) runs
inside one `conn.transaction()` so candidate rows read at the top of the batch
stay consistent with the writes made against them. A failure processing one
email is caught and appended to `summary["errors"]`; it does not abort the
batch (mirrors the "degrade, don't crash" convention used by the other
components' per-item loops).

## The live `job_applications` schema (quirks)

The table already exists in the TigerCloud instance, created out-of-band, with
**no primary key** — rows are addressed by their physical `ctid` within a
single transaction (`gmail_persistence.fetch_open_candidates` selects `ctid::text`;
`attach_thread_to_row` updates `WHERE ctid = $1::tid`). Columns:

| Column | Type | Notes |
|---|---|---|
| `user_id` | `uuid` | Application owner. |
| `job_id` | `uuid` | Nullable — left `NULL` on agent-inserted rows since there may be no matching `jobs` row and the table has no FK constraint. |
| `aplied_date` | `timestamptz` | **Misspelled in the live schema** ("aplied", not "applied") — do not "fix" the spelling without also migrating the column, or every insert breaks. |
| `metadata` | `jsonb` | Free-form; the agent stores `{source, company, role, status, subject, sender, snippet, thread_id}` here (see `gmail_agent._build_metadata`). |
| `is_completed` | `boolean` | The **"applied" flag** — the agent sets it `true` on both attach and insert, since a completion email is the definition of "applied" for this feature. |
| `thread_id` | `text` | Retyped from `uuid` by migration 003 (Gmail thread ids are short hex strings, not UUIDs) and given a partial unique index (`WHERE thread_id IS NOT NULL`) so one thread can never attach to two rows. |

`gmail_persistence.fetch_open_candidates` pulls `company`/`role` from `metadata`
first (keys `company`/`employer`/`organization`, then `role`/`title`/`position`
via `_first_str`), falling back to a joined `jobs.title` for role when
`metadata` has none.

## Gmail transport switch and OAuth

`GMAIL_TRANSPORT` (env var, default `mcp`) selects between two transports in
`gmail_mcp_client.py`, both producing the identical normalized shape
`{thread_id, subject, sender, snippet}` so the rest of the pipeline is
transport-agnostic:

- **`mcp`** (primary): the official Google Gmail MCP server
  (`https://gmailmcp.googleapis.com/mcp/v1`, streamable-HTTP) via the `mcp`
  Python SDK. OAuth uses `GMAIL_OAUTH_CLIENT_ID`/`GMAIL_OAUTH_CLIENT_SECRET`
  with a one-shot local HTTP server (`_wait_for_oauth_callback`) to capture the
  redirect; tokens persist to `.gmail_mcp_token.json` via `_FileTokenStorage`.
- **`rest`** (fallback): direct Gmail REST API via
  `google-api-python-client` + `google-auth-oauthlib`, using a Desktop-app
  OAuth client secret (`GMAIL_CLIENT_SECRET_PATH`) and caching the resulting
  token at `GMAIL_TOKEN_PATH`.

Both transports need a sub-day time window, which Gmail's `newer_than:`
operator does not support (day/month/year units only) — `gmail_query` builds
`newer:<epoch-seconds>` instead. `auth_status()` reports `{transport,
authorized}` by checking for the relevant token file on disk, without making a
Gmail call. `python gmail_agent.py auth` (`authorize()`) triggers/verifies
OAuth with a minimal 1-hour thread search.

## Which file to change for a bug

| Symptom | Start here |
|---|---|
| A non-completion email (rejection, interview invite, newsletter) gets tracked | `gmail_schemas.py` — `CLASSIFY_SYSTEM_INSTRUCTION`, `gmail_classifier.py` — `min_confidence` handling |
| A completion email is dropped that should qualify | `gmail_classifier.py` — `classify_threads` (confidence threshold, `by_id` lookup keyed on `thread_id`) |
| Email attaches to the wrong open application row | `gmail_schemas.py` — `MATCH_SYSTEM_INSTRUCTION`, `gmail_matcher.py` — `match_emails` confidence threshold |
| Same thread creates a duplicate row on a re-run | `gmail_persistence.py` — `thread_exists`, migration 003's partial unique index on `thread_id` |
| Two emails race to claim the same open row | `gmail_persistence.py` — `attach_thread_to_row` (`WHERE thread_id IS NULL` guard, `"UPDATE 0"` fallback to insert) |
| Insert fails with a type error on `aplied_date`/`job_id`/`thread_id` | `gmail_persistence.py` — `insert_new_row`; check the live column types haven't drifted again (see migration 003) |
| Gmail auth fails, or `auth_status` reports unauthorized incorrectly | `gmail_mcp_client.py` — `_build_mcp_oauth_provider` / `_build_gmail_service`, `GMAIL_TRANSPORT` value, token file paths |
| Scheduler never runs, or runs when it shouldn't | `gmail_scheduler.py` — `start_gmail_scheduler`; umbrella `main.py`'s `GMAIL_SCHEDULER_ENABLED` check |
| `POST /api/gmail/sync` returns 400 unexpectedly | `gmail_agent.py` — `create_fastapi_router`, `_resolve_dsn`, missing `GEMINI_API_KEY`/`DATABASE_URL` |
| Wrong `user_id` used for candidates/inserts | `gmail_agent.py` — `sync_recent_threads` uid resolution order (`user_id` arg -> `UPJOB_USER_ID` env -> `resolve_default_user_id`) |

## Testing

All tests are offline stdlib `unittest`. Run the suite:

```bash
python -m unittest discover -s tests -t .
```

Conventions to preserve when adding tests:
- Stub the lazy `from google import genai` import by injecting a fake
  `google.genai` module into `sys.modules` before calling `sync_recent_threads`
  (see `tests/test_gmail_agent.py::_install_fake_genai`).
- Patch collaborators on the `gmail_agent` module object itself
  (`unittest.mock.patch.object(gmail_agent, "name", ...)`), not on their
  defining modules — `gmail_agent.py` imports them by name at module load time.
- Use a fake async connection object supporting `async with conn.transaction():`
  and `await conn.close()` (see `tests/test_gmail_agent.py::_FakeConn`) rather
  than importing `asyncpg`.
- Never hit a live Gmail account, database, or Gemini API in unit tests.
