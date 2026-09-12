# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This `backend` directory contains several standalone components plus one shared package, each its own project root (there is no umbrella backend service wiring them together yet):

- `simplify-scraper/` — discovers and extracts new SimplifyJobs internship postings with CloakBrowser + Gemini.
- `repo-analyzer/` — ingests a GitHub repository URL and profiles the skills it demonstrates, persisting a project specification, a `DETAILS.md` document, and a pgvector embedding. Implements the spec in `RepoAnalyzer.md`.
- `resume-tailor/` — builds a tailored LaTeX resume by matching a user's analyzed projects (from repo-analyzer) against a job specification, then generating and reviewing bullet points. Implements the spec in `ResumeTailor.md`.
- `reward-handler/` — centralized progression, economy, and verification engine with streak state machine, Redis leaderboards, and tiered subagent token optimization. Implements the spec in `RewardHandler.md`.
- `common/` — shared helpers imported by repo-analyzer, resume-tailor, and reward-handler: `db.py` (connection/DSN/vector/timestamp), `gemini.py` (`generate_json` + retry/backoff), `embeddings.py` (`gemini-embedding-001`, L2 normalization), `latex.py` (escaping), `models.py` (model identifiers).

Backend-wide status and the TODO list live in `PROGRESS.md`. Each feature has a `DOCUMENTATION.md` (navigation + file map + symptom→file table) and a `README.md` (setup + usage).

All components follow the same conventions: a single async public entry point callable from a FastAPI route (returns only JSON-serializable data), stdlib `unittest` (no pytest), and heavy third-party imports (browser/AI/DB SDKs) done lazily inside functions so pure logic is testable without them installed.

### The `common` package and sys.path

`common` lives at the `backend/` level, but each component runs from its own directory, so `backend/` is not on `sys.path` by default. Every entry point and test module that imports `common` must first prepend `backend/` with a raw bootstrap (it cannot live inside `common` — importing it would already require the path):

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[N]))  # backend/
```

`N` is the number of directories up to `backend/` (1 from a component file, 2 from a component's `tests/`). repo-analyzer re-exports the moved helpers from its own `persistence.py`/`embeddings.py`/`analyzer.py` for backward compatibility, so its public module API is unchanged.

## simplify-scraper (run from `simplify-scraper/`)

Setup:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m cloakbrowser install
printf 'GEMINI_API_KEY=your-key\n' > .env
```

Run the scraper:
```bash
python simplify_scraper.py --max-jobs 10
```

Run tests:
```bash
python -m unittest tests/test_simplify_scraper.py
# Single test case or method:
python -m unittest tests.test_simplify_scraper.ScraperTests
python -m unittest tests.test_simplify_scraper.ScraperTests.test_parse_feed_handles_continuations_flags_locations_and_categories
```

### Architecture

`simplify_scraper.py` is a single-file pipeline with one public async entry point, `scrape_new_jobs`, designed to be called directly from a FastAPI route (it returns only JSON-serializable data). The pipeline has four stages:

1. **Feed fetch + parse** (`parse_feed`, `_TableParser`): Fetches the SimplifyJobs Summer 2027 internships README (raw Markdown containing embedded HTML tables) and parses it with a dependency-free `HTMLParser` subclass rather than a full HTML/Markdown library. Postings are grouped under `## ... Internship Roles` Markdown headings (one per category); rows starting with `↳` continue the previous row's company. `application_url` (the non-Simplify link in each row) is the stable identity used for dedup and state tracking; tracking query params are stripped in `_clean_url`.

2. **Change detection / state** (`discover_new`, `_load_state`, `_save_state`): A local JSON state file (default `.simplify_scraper_state.json`) tracks `known_application_urls` (already scraped) and `pending_application_urls` (discovered but not yet scraped, e.g. because `--max-jobs` cut off a batch). On the very first stateful run (no state file), "new" is determined by the feed's own `age_days` (an `Nd` value parsed from the Age column) rather than by identity, via `first_run_max_age_days`. All state file I/O writes to a temp file and renames atomically.

3. **Browse + extract** (`_scrape_one`, `_clean_page_html`, `extract_job_details_with_ai`): For each selected posting, opens the application URL with CloakBrowser (`cloakbrowser.launch_async`), waits briefly for client-side rendering, strips scripts/styles/tracking attributes from the DOM in-page via `page.evaluate`, truncates HTML to a character budget (keeping head and tail, since job details are usually at either end), and sends it to Gemini (`google-genai`) with a structured JSON schema (`AI_EXTRACTION_SCHEMA`) for extraction. Runs are bounded by an `asyncio.Semaphore` for concurrency control. A failure in one posting (browser or AI) is caught and converted into an `scrape_status: "error"` result (`_error_result`) rather than aborting the whole batch — this is intentional and should be preserved in any changes to this stage.

4. **Result shaping**: Every posting's output — success or error — conforms exactly to the shape in `sample.json`/`example.json`: feed-derived fields (`company`, `role`, `category`, `application_url`, `age_days`) are always present even on error, and AI-derived fields (`date_posted`, `valid_through`, `employment_type`, `description`, `requirements`, `skills`) fall back to feed data or empty values when extraction fails or omits them.

CLI (`main`/`_build_parser`) and the FastAPI-callable async function are thin wrappers over the same `scrape_new_jobs` call — keep new options plumbed through both.

## repo-analyzer (run from `repo-analyzer/`)

Setup:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GITHUB_PAT, GEMINI_API_KEY, DATABASE_URL
```

Apply the database schema once (Postgres + pgvector; a TigerCloud instance is the target):
```bash
# migrations/001_init.sql is applied automatically on first run by ensure_schema,
# or apply it by hand against DATABASE_URL with psql.
```

Run the analyzer:
```bash
python repo_analyzer.py https://github.com/owner/repo
python repo_analyzer.py owner/repo --no-persist --details-out DETAILS.md
python repo_analyzer.py owner/repo --user-context '{"metrics":["1M rows/day"]}' --char-budget 4000
```

Run tests (fully offline — no network, no database, no model SDK required):
```bash
python -m unittest discover -s tests -t .
# Single module:
python -m unittest tests.test_analyzer
```

### Architecture

The public entry point is `analyze_repository` in `repo_analyzer.py` — async, returns only JSON-serializable data, and wraps the CLI (`main`/`_build_parser`). Keep new options plumbed through both.

The pipeline is deliberately **not** an agent loop and uses **no** GitHub MCP server or LangChain (a deviation from `RepoAnalyzer.md`, decided because an agent loop resends its transcript each turn and its per-file read cap existed only to bound that cost). Instead it is deterministic bulk ingestion plus a long-context Gemini call:

1. **Parse + identity** (`ingestion.parse_repo_url`, `canonical_repo_url`): Resolves any accepted URL spelling (bare `owner/repo`, `.git` suffix, `/tree/<branch>`, `www.`, SCP-style) into `(owner, repo, ref)`. The **canonical** URL (`https://github.com/owner/repo`) is the stored identity used for the `projects.github_repo_url` unique constraint — never the spelling the caller passed, or each spelling would create a duplicate row.

2. **Ingest** (`ingestion.fetch_repository_files` → `iter_tarball_files`): Downloads the repo as a single tarball snapshot (one request for the whole tree, not one per file) and filters it while streaming (`tarfile` `"r|gz"`, never extracted to disk). Excludes noise dirs (`node_modules`, `.venv`, `dist`, …), binary/asset extensions, lockfiles, minified/generated files, and oversized files; byte/file-count caps bound what a compressed archive can expand into. The blocking download runs via `asyncio.to_thread` so it does not stall the event loop — it is intentionally sync internally because `tarfile` drives the transfer with sequential `read(n)` calls. `sort_files` orders by signal (README/manifests first, tests last) so budget truncation drops the least informative tail.

3. **Commit dates** (`commits.fetch_commit_bounds`): Resolves earliest/latest commit timestamps deterministically via the commits API and the `Link rel="last"` header — two requests, no pagination crawl. The model is never asked to compute dates. Failure here is non-fatal (returns `(None, None)`). The tarball fetch and commit lookup are overlapped with `asyncio.gather`.

4. **Analyze** (`analyzer.analyze_files`): Adapts to size, not configuration. A codebase that fits `char_budget` takes one long-context call against `ANALYSIS_SCHEMA`. One that does not is chunked along **module boundaries** (`chunk_files`), each chunk analyzed in parallel under a semaphore into a JSON fragment (`FRAGMENT_SCHEMA`) with a shared global header (path tree + README + manifest, so no chunk is read blind), merged by deterministic set-union in Python (`schemas.merge_fragments` — no model in the merge), then a single reduce call writes the summary and collapses near-duplicate names. Map-reduce, not sequential refinement (which resends growing state and lets late chunks overwrite early findings). Transient API errors (429/5xx) retry with jittered backoff; this matters most in the map phase, where one failed chunk would otherwise discard the rest.

5. **Shape + persist** (`schemas`, `details_md`, `persistence`, `embeddings`): `build_specification`/`build_details` coerce model output into fixed shapes with all fields present even on failure. `render_details_md` is a pure serializer (no model) mapping each `details` key to a `##` heading. `upsert_project` writes the spec to `projects` (arrays as native `TEXT[]`, details as `JSONB`) keyed on the canonical URL, preserving `spec_created_at` and advancing `spec_updated_at`. `replace_architecture_embedding` stores one `gemini-embedding-001` vector (768-dim) of the architectures list in `project_architectures`, FK to `projects.id` with cascade delete.

A failure in any stage degrades rather than aborts: the envelope from `schemas.analysis_result` keeps its shape, `analysis_status` becomes `"error"`/`"partial"`, and deterministically-resolved fields (identity, commit dates) survive.

### Model and embedding notes

- Generation model default is `gemini-3.5-flash-lite`; embeddings use `gemini-embedding-001` (defined in `common/models.py`, re-exported by `schemas`). `gemini-2.5-flash` and `text-embedding-004` are not available on the current key.
- `gemini-embedding-001` returns unit-length vectors only at its native 3072 dims; reduced-dimension (768) output is **not** normalized by the API, so `common/embeddings.py` L2-normalizes it before storage.

## resume-tailor (run from `resume-tailor/`)

Setup:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY, DATABASE_URL (same DB as repo-analyzer)
```

Run the tailor (the job spec is inline JSON or a path to a JSON file following `ResumeTailor.md` section 1):
```bash
python resume_tailor.py job.json --user-id <id> --candidate-name "Jane Doe" --output tailored_resume.tex
python resume_tailor.py job.json --user-id <id> --top-k 3 --json-out result.json
```

Run tests (offline — no DB, network, or model):
```bash
python -m unittest discover -s tests -t .
```

### Architecture

The public entry point is `tailor_resume` in `resume_tailor.py` — async, returns a JSON-serializable envelope, wraps the CLI. The pipeline (detailed in `resume-tailor/DOCUMENTATION.md`):

1. **Retrieval** (`retrieval.py`): embed the job's `Architecture` in memory (never stored), then stage 2B — a PostgreSQL hard-skill overlap filter (`OVERLAP_SQL`, case-insensitive, scoped by `user_id`, 0.8 threshold with a best-by-overlap fallback so it always returns candidates) — then stage 2C — an in-memory cosine rank over the stored embeddings (cosine == dot product because both are L2-normalized) — returning the top 3–4 projects with their `details_markdown`. It reads the repo-analyzer tables and never writes them.
2. **Generation** (`generation.py`): a two-pass Gemini chain — `SYSTEM_PROMPT_GENERATE` (Google-XYZ bullets) then `SYSTEM_PROMPT_REVIEW` (strip first-person, strengthen verbs, enforce a metric), both verbatim from the spec — bounded-concurrent across projects.
3. **LaTeX injection** (`latex_resume.py` + `templates/resume_template.tex`): a pure serializer that escapes all text via `common.latex` and `str.replace`s the `{{CANDIDATE_NAME}}`/`{{PROJECTS}}` tokens. Output compiles with `pdflatex`.

**Critical gotcha:** retrieval must embed with the embedding model, not the generation model — do not forward `tailor_resume`'s generation `model` into `select_projects` (it falls back to `EMBEDDING_MODEL`). Passing a generation model yields a 404 on `embedContent`. A resume bullet's mandated metric (spec §4) is often fabricated by the model — inherent to the spec.

## reward-handler (run from `reward-handler/`)

Setup:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL, optional REDIS_URL, optional GEMINI_API_KEY
```

Run CLI:
```bash
python reward_handler.py status <user_id>
python reward_handler.py set-tier <user_id> 2
python reward_handler.py redeem <user_id> streak_freeze
python reward_handler.py leaderboard --type weekly --limit 10
python reward_handler.py verify-app <user_id> <job_id> <hash> --type EMAIL
python reward_handler.py verify-quest <user_id> <quest_id> "Distributed Container Engine" 95
python reward_handler.py rollover
python reward_handler.py weekly-reset
```

Run tests (offline — no DB, Redis, or model SDK required):
```bash
python -m unittest discover -s tests -t .
# Single module:
python -m unittest tests.test_economy
python -m unittest tests.test_subagents
```

### Architecture

The public async entry points live in `reward_handler.py` (`get_user_economy_status`, `set_user_daily_tier`, `redeem_item`, `fetch_leaderboard`, `verify_job_application`, `verify_architectural_quest`, `run_midnight_rollover`, `run_weekly_reset`) and wrap the CLI. Keep new options plumbed through both.

1. **Verification Ingestion** (`verification.py`):
   - Daily job applications check `verification_logs` for `confirmation_hash` to reject duplicates with `409 Conflict` (anti-cheat). Awards 100 XP, 50 base bytes on first app of the day, and tier bonus bytes multiplied by streak multiplier when reaching daily target.
   - Quests award base 500 XP, 100 Cores, 200 Bytes, +50 Cores high-quality bonus (score >= 90), and append the component to verified architectures.

2. **Streak State Machine & Rollover** (`economy.py`):
   - Computes daily transitions at midnight UTC: if yesterday's apps >= target, increment streak and check milestones (Day 7 bronze badge + 50 Cores, Day 30 gold badge + 250 Cores).
   - If target was missed, consumes an active `streak_freeze` to preserve streak; otherwise resets streak to 0.
   - Evaluates lazily when user calls `/api/economy/me` to catch up if cron was delayed.

3. **Shop Redemptions** (`economy.py`):
   - Executes atomic debit using `SELECT ... FOR UPDATE` row-level locks on `user_economy`. Allocates freezes, cosmetics, or functional platform unlock flags (`unlocked_features`).

4. **Redis Leaderboards & In-Memory Fallback** (`leaderboard.py`):
   - Pipelined atomic `ZINCRBY` updates to `leaderboard:all_time` and `leaderboard:weekly` on every XP change.
   - Monday 00:00:00 UTC weekly reset snapshots top 10 to PostgreSQL `weekly_leaderboard_snapshots` and clears `leaderboard:weekly`.
   - `InMemoryRedisLeaderboard` provides pure-Python sorted sets for local dev without a live Redis server.

5. **Token Optimization via Tiered Subagents** (`subagents.py`):
   - 3-tier in-process funnel: Tier 0 deterministic cache (by commit SHA) and file filter (0 tokens) → Tier 1 Scout Subagent (`gemini-3.5-flash-lite`, ~800 tokens) pinpoints 1–2 relevant files → AST structural code extraction (0 tokens) → Tier 2 Evaluator Subagent (`gemini-3.5-flash-lite`, ~1,500 tokens) strictly scores code against rubric.
   - Reduces tokens per evaluation from ~120,000 down to ~2,500 (>97% reduction).

## Conventions (all components)

- No linter/formatter is configured.
- Tests are stdlib `unittest`. Keep them offline: stub SDKs in `sys.modules`, build tarballs in memory, use fake DSNs — never hit a real network, database, or model.
- `.env` holds live credentials and is gitignored in each component; never commit it, and never put real credentials in `.env.example` or `README.md`.
