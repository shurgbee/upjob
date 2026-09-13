# gmail-agent

Scans recent Gmail threads for job-application *completion* emails (automated
"thank you for applying" confirmations) and reconciles them to the user's
`job_applications` rows: attaching the thread to an existing open row when one
plausibly matches, or creating a new completed row when none does.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY, DATABASE_URL, and the Google OAuth client
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (for live runs) | PostgreSQL connection string (same TigerCloud instance as the other components). |
| `GEMINI_API_KEY` | Yes (for live runs) | Used for both the classification and matching Gemini calls. |
| `UPJOB_USER_ID` | Optional | Owner of the rows this agent reconciles. Defaults to the sole `app_users` row when unset. |
| `GMAIL_TRANSPORT` | Optional | `mcp` (default, official Gmail MCP server) or `rest` (Gmail REST API fallback). |
| `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` | Required for `mcp` transport | Google OAuth client used for the Gmail MCP server's OAuth flow. |
| `GMAIL_CLIENT_SECRET_PATH` / `GMAIL_TOKEN_PATH` | Required for `rest` transport | Path to a Desktop-app OAuth client secret JSON and the cached token next to it. |
| `GMAIL_SCHEDULER_ENABLED` | Optional | Set to `0` to disable the umbrella app's 2-hour background sync. |

### Database migration

Migrations run automatically via `ensure_schema(conn)` on first run, or apply manually:
```bash
psql $DATABASE_URL -f migrations/003_job_applications_thread_id_text.sql
```

## Authorize Gmail access

First run triggers the OAuth consent flow for the configured transport and
persists the token to disk (`.gmail_mcp_token.json` or `.gmail_token.json`):

```bash
python gmail_agent.py auth
```

Check whether a usable token is already present without touching Gmail:
```bash
python gmail_agent.py sync --hours 0   # not needed; see GET /api/gmail/auth/status below
```

## Run a sync

```bash
# Scan the last 2 hours (default) and reconcile
python gmail_agent.py sync

# Scan a wider window, or for a specific user
python gmail_agent.py sync --hours 6 --user-id <uuid>

# Use a different database
python gmail_agent.py --dsn postgresql://... sync
```

Output is the JSON summary `{scanned, qualified, skipped_existing, matched, created, errors}`.

## Running Tests

All tests run completely offline with no network, Gmail, PostgreSQL, or Gemini access required:

```bash
python -m unittest discover -s tests -t .

# Single test module
python -m unittest tests.test_gmail_agent
python -m unittest tests.test_classifier
python -m unittest tests.test_matcher
python -m unittest tests.test_persistence
python -m unittest tests.test_schemas
```

## HTTP routes

`create_fastapi_router(dsn=None, gemini_api_key=None)` in `gmail_agent.py` returns
an `APIRouter` mountable on the umbrella FastAPI app:

- `POST /api/gmail/sync` — body `{"hours": 2, "user_id": null}`, returns the same
  summary dict as the CLI's `sync` subcommand.
- `GET /api/gmail/auth/status` — `{"transport": "mcp"|"rest", "authorized": bool}`,
  reports whether a usable OAuth token is present without calling Gmail.

## Background scheduler

The umbrella app starts a 2-hour `AsyncIOScheduler` job (`gmail_scheduler.py`,
`start_gmail_scheduler`) that calls the same `sync_recent_threads` entry point as
the HTTP route; a failed run is logged, never raised, so it never kills the
scheduler. Set `GMAIL_SCHEDULER_ENABLED=0` in the umbrella app's environment to
disable it (e.g. for local development where you'd rather trigger syncs by hand).
