# Progress — repo-analyzer

Status as of 2026-09-12. Implements `../RepoAnalyzer.md` (Feature #1: GitHub
repository ingestion & skill profiling).

## Done and verified

The pipeline runs end to end and has been exercised against the live GitHub and
Gemini APIs and the TigerCloud database.

- **Ingestion** — single tarball download, streamed and filtered, with byte /
  file-count / per-file caps. Off-loop via `asyncio.to_thread`.
- **Commit dates** — earliest/latest resolved deterministically from the commits
  API `Link rel="last"` header (two requests), overlapped with the fetch.
- **Analysis** — single long-context call when the codebase fits `char_budget`;
  map-reduce over module-boundary chunks when it does not. Transient API errors
  (429/5xx) retry with jittered backoff.
- **Shaping** — all output fields present even on failure; `analysis_status`
  degrades to `error`/`partial` without losing deterministic fields.
- **DETAILS.md** — pure serializer, no model in the loop.
- **Persistence** — `projects` row (TEXT[] arrays, JSONB details) keyed on the
  canonical URL; `project_architectures` holds one 768-dim, L2-normalized
  `gemini-embedding-001` vector, FK to `projects.id` with cascade delete.
- **Tests** — 135 offline stdlib `unittest` tests (no network, DB, or SDK).

### Verification evidence

- `pypa/sampleproject` → `analysis_status: ok`, 11 files, single-pass, ~16s.
  Commit dates 2013-12-03 → 2024-11-06. `user_context` metrics folded into
  `Metrics` alongside code-derived figures.
- Map-reduce path confirmed by forcing `--char-budget 4000`: 4 chunks + 1
  reduce = 5 calls, still `ok`.
- Two different URL spellings of the same repo upsert to **one** row
  (`project_id` stable, `spec_created_at` preserved, `spec_updated_at` advanced).
- Stored embedding verified at L2 = 1.000000; cascade delete confirmed.

## What needs to be worked on

### Integration (the real next step)

- [ ] **FastAPI route.** `analyze_repository` is built to be called from one and
      returns JSON-serializable data, but nothing mounts it yet. Decide
      sync-vs-background: a large repo takes tens of seconds, too long for a
      blocking HTTP request. See `repo_analyzer.py::analyze_repository`.
- [ ] **User ownership.** `projects` has no `user_id`; there is no notion of which
      user a project belongs to. Needs a column + migration + plumbing from the
      (WorkOS) auth context. Touches `migrations/001_init.sql` (or a new
      migration), `persistence.py::upsert_project`, and the entry point.
- [ ] **Frontend wiring.** Not connected to `../../frontend` (Next.js/WorkOS).

### Hardening / correctness

- [ ] **ivfflat index is created empty.** pgvector's ivfflat needs training data
      to be meaningful; at current row counts it does nothing. A `diskann`
      (pgvectorscale) upgrade is noted but unused. Revisit before real volume.
      See `migrations/001_init.sql`.
- [ ] **No retry/caching on the tarball fetch** — only Gemini calls retry. A
      flaky download fails the run. See `ingestion.py::_fetch_repository_files_blocking`.
- [ ] **Private repos** work if the PAT has scope, but that path is untested.
- [ ] **No similarity-search read path.** Embeddings are stored for "subsequent
      similarity matching" (per spec) but nothing queries them yet — presumably
      a later feature that consumes this one.

### Housekeeping

- [ ] **Rotate the three credentials** (GitHub PAT, Gemini key, TigerCloud
      password) — they were pasted in plaintext in chat on 2026-09-12.
- [x] Commit `RepoAnalyzer.md` (the spec).
- [x] Document the component in `../CLAUDE.md`.
