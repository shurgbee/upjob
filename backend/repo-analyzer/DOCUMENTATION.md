# Documentation — repo-analyzer

How the feature works, how to navigate the code, and which file to open for a
given bug. For setup and commands see `README.md`; for status and the TODO list
see `../PROGRESS.md` (backend-wide); for the original spec see `../RepoAnalyzer.md`.

## What it does

Given a GitHub repository URL (+ optional author-supplied context), it analyzes
the codebase and produces three artifacts:

1. A structured **project specification** (name, description, dates,
   technologies, architectures) → a row in the Postgres `projects` table.
2. A **`DETAILS.md`** skill document (Summary, Architectural Components, Core
   Competencies, Technologies, Actions, Metrics).
3. A **pgvector embedding** of the architectures list → `project_architectures`,
   for later similarity matching.

The design is deliberately **not** an agent loop and uses no GitHub MCP server or
LangChain. It is deterministic bulk ingestion (one tarball download) followed by
a single long-context Gemini call, falling back to map-reduce only when a repo
is too big to fit one call. The rationale is in `../CLAUDE.md`.

## File map

| File | Responsibility |
|---|---|
| `repo_analyzer.py` | Public entry point `analyze_repository` + CLI. Orchestrates every stage; owns error degradation and canonical identity. |
| `ingestion.py` | URL parsing, canonical identity, tarball download, streaming filter, file ordering, budget packing, module-boundary chunking. |
| `commits.py` | Deterministic earliest/latest commit dates via the commits API `Link` header. |
| `analyzer.py` | The Gemini layer: single-pass vs map-reduce branching, prompts, schema enforcement, retries, concurrency. |
| `schemas.py` | The contract: Gemini response schemas, `ProjectSpecification`, set-union merging, result shaping. Pure, stdlib-only. |
| `details_md.py` | Pure serializer: `details` dict → Markdown. No model. |
| `persistence.py` | asyncpg: DSN handling, schema apply, `upsert_project`, `replace_architecture_embedding`. |
| `embeddings.py` | `gemini-embedding-001` call + L2 normalization of reduced-dim vectors. |
| `migrations/001_init.sql` | `projects` and `project_architectures` tables, indexes, pgvector extension. |
| `tests/` | Offline stdlib `unittest` — one module per source file. |

## Data flow

```
analyze_repository(url, user_context, ...)          [repo_analyzer.py]
  │
  ├─ parse_repo_url(url) → (owner, repo, ref)        [ingestion.py]
  ├─ canonical_repo_url(owner, repo)  ← stored identity
  │
  ├─ asyncio.gather(                                 ← overlapped
  │     fetch_repository_files(...)  → [RepoFile]    [ingestion.py] (off-loop)
  │     fetch_commit_bounds(...)     → (start, end)  [commits.py]
  │  )
  │
  ├─ analyze_files(files, dates, user_context, ...)  [analyzer.py]
  │     sort_files → build_global_header
  │     ├─ fits char_budget?  → one call (ANALYSIS_SCHEMA)         single_pass
  │     └─ else → chunk_files → N parallel fragment calls          map_reduce
  │                merge_fragments (Python set-union)  [schemas.py]
  │                → one reduce call (ANALYSIS_SCHEMA)
  │
  ├─ build_specification / build_details             [schemas.py]
  ├─ render_details_md(details)                      [details_md.py]
  │
  └─ _persist(...)                                   [repo_analyzer.py]
        upsert_project (ON CONFLICT canonical url)   [persistence.py]
        embed_architectures → L2-normalize           [embeddings.py]
        replace_architecture_embedding               [persistence.py]
  → analysis_result envelope (+ project_id)          [schemas.py]
```

Returns the JSON envelope from `schemas.analysis_result` — identical shape for
success and failure, plus `project_id` when persisted.

## Key invariants (do not break these)

- **Canonical identity.** The `projects` row is keyed on
  `https://github.com/owner/repo`, never the raw URL the caller typed. Any write
  path must use `canonical_repo_url`, or duplicate rows appear per spelling.
- **Off-loop download.** `fetch_repository_files` delegates the blocking,
  synchronous tarball stream to `asyncio.to_thread`. It is sync internally on
  purpose — `tarfile` drives the transfer with sequential `read(n)` calls and
  cannot consume an async iterator without buffering the whole archive.
- **The model never computes dates.** Timestamps come from `commits.py` and are
  passed into analysis as facts.
- **The merge has no model in it.** `merge_fragments` is deterministic Python
  set-union; only the final reduce call touches a model.
- **Degrade, don't abort.** A stage failure returns the envelope with
  `analysis_status` = `error`/`partial` and deterministic fields intact.
- **Heavy SDKs are lazy.** `httpx`, `asyncpg`, `google-genai` are imported inside
  functions so the pure logic and the test suite run without them.
- **Reduced-dim embeddings are L2-normalized** before storage — the API only
  normalizes its native 3072-dim output.

## Which file to change for a bug

| Symptom | Start here |
|---|---|
| A URL form is rejected or mis-parsed; duplicate `projects` rows for one repo | `ingestion.py` — `parse_repo_url`, `canonical_repo_url` |
| Wrong files included/excluded (e.g. `node_modules` leaking in, a real source file dropped) | `ingestion.py` — `EXCLUDED_*` constants, `exclude_reason`, `looks_generated` |
| Large repo: chunk boundaries wrong, budget overflow, too many/few chunks | `ingestion.py` — `pack_files`, `chunk_files`; `analyzer.py` — `char_budget`, `MAX_CHUNKS` |
| `start_time`/`end_time` null or wrong | `commits.py` — `parse_last_page`, `extract_commit_date`, `fetch_commit_bounds` |
| Bad/empty extraction, schema mismatch, prompt tuning | `analyzer.py` — prompts, `_generate_json`; `schemas.py` — `ANALYSIS_SCHEMA`, `FRAGMENT_SCHEMA` |
| Transient 429/5xx kills a run | `analyzer.py` — `is_retryable_error`, `RETRYABLE_STATUS_CODES`, `MAX_ATTEMPTS` |
| `DETAILS.md` formatting (headings, bullets, placeholders) | `details_md.py` — `render_details_md` |
| DB connection / SSL / `sslmode` errors | `persistence.py` — `normalize_dsn`, `ssl_argument`, `connect` |
| Rows not saved, upsert overwrites wrong fields, timestamps wrong | `persistence.py` — `upsert_project`; `migrations/001_init.sql` |
| `vector` / dimension / cast errors on insert | `persistence.py` — `format_vector_literal`, `replace_architecture_embedding`; `embeddings.py` — `EMBEDDING_DIMENSIONS` |
| Embeddings not unit-length; similarity scores off | `embeddings.py` — `embed_architectures`, `normalize_vector` |
| "Model not found / unavailable" | `schemas.py` — `DEFAULT_GEMINI_MODEL`, `EMBEDDING_MODEL` (see model notes in `../CLAUDE.md`) |
| Schema/table/index changes | `migrations/001_init.sql` (idempotent — safe to re-run) |
| CLI flag or option plumbing | `repo_analyzer.py` — `_build_parser`, `main`, and `analyze_repository` (keep both in sync) |

## Testing

All tests are offline stdlib `unittest`. Run the suite:

```bash
python -m unittest discover -s tests -t .
```

Conventions to preserve when adding tests:
- Stub the Gemini SDK by installing a fake module in `sys.modules` (see
  `tests/test_analyzer.py::fake_genai`).
- Build tarballs in memory with `tarfile` + `io.BytesIO` (see
  `tests/test_ingestion.py::make_tarball`) — never download.
- Use fake DSNs and never connect to a real database; `persistence.py`'s pure
  helpers (`normalize_dsn`, `format_vector_literal`, `parse_timestamp`) are the
  tested surface.

For a true end-to-end check you need real `GITHUB_PAT`, `GEMINI_API_KEY`, and
`DATABASE_URL` in `.env`; run against a small public repo with
`python repo_analyzer.py owner/repo`.
