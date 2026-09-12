# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This `backend` directory contains two standalone components, each its own project root (there is no umbrella backend service wiring them together yet):

- `simplify-scraper/` — discovers and extracts new SimplifyJobs internship postings with CloakBrowser + Gemini.
- `repo-analyzer/` — ingests a GitHub repository URL and profiles the skills it demonstrates, persisting a project specification, a `DETAILS.md` document, and a pgvector embedding. Implements the spec in `RepoAnalyzer.md`.

Both follow the same conventions: a single async public entry point callable from a FastAPI route (returns only JSON-serializable data), stdlib `unittest` (no pytest), and heavy third-party imports (browser/AI/DB SDKs) done lazily inside functions so pure logic is testable without them installed.

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

- Generation model default is `gemini-3.6-flash`; embeddings use `gemini-embedding-001` (see `schemas.DEFAULT_GEMINI_MODEL` / `EMBEDDING_MODEL`). `gemini-2.5-flash` and `text-embedding-004` are not available on the current key.
- `gemini-embedding-001` returns unit-length vectors only at its native 3072 dims; reduced-dimension (768) output is **not** normalized by the API, so `embeddings.embed_architectures` L2-normalizes it before storage.

## Conventions (both components)

- No linter/formatter is configured.
- Tests are stdlib `unittest`. Keep them offline: stub SDKs in `sys.modules`, build tarballs in memory, use fake DSNs — never hit a real network, database, or model.
- `.env` holds live credentials and is gitignored in each component; never commit it, and never put real credentials in `.env.example` or `README.md`.
