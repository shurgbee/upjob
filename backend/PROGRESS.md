# Backend progress

Status tracker for the `upjob` backend, one section per feature plus shared
concerns. Update the relevant section whenever a feature lands or a TODO is
resolved. Last updated 2026-09-12.

Components:
- `simplify-scraper/` — SimplifyJobs internship scraper (pre-existing).
- `repo-analyzer/` — Feature #1: GitHub repo ingestion & skill profiling. Implements `RepoAnalyzer.md`.
- `resume-tailor/` — Feature #2: semantic resume tailor. Implements `ResumeTailor.md`. Consumes repo-analyzer's data.
- `reward-handler/` — Feature #3: progression, economy & verification engine with subagent token optimization. Implements `RewardHandler.md`.
- `common/` — shared helpers (db, gemini, embeddings, latex, models) imported by all features.

---

## Feature #1 — repo-analyzer

### Done and verified (live against GitHub, Gemini, TigerCloud)

- **Ingestion** — single tarball download, streamed and filtered, byte / file-count / per-file caps. Off-loop via `asyncio.to_thread`.
- **Commit dates** — earliest/latest from the commits API `Link rel="last"` header (two requests), overlapped with the fetch.
- **Analysis** — single long-context call when the codebase fits `char_budget`; map-reduce over module-boundary chunks otherwise. Transient 429/5xx retry with jittered backoff.
- **Shaping** — all output fields present even on failure; `analysis_status` degrades to `error`/`partial` without losing deterministic fields.
- **DETAILS.md** — pure serializer, no model in the loop.
- **Persistence** — `projects` row (TEXT[] arrays, JSONB details) keyed on the canonical URL; `project_architectures` holds one 768-dim L2-normalized `gemini-embedding-001` vector, FK to `projects.id` with cascade delete.
- **user_id** — `projects.user_id` (migration 002) plumbed through `upsert_project`, `analyze_repository`, and the CLI.
- **Tests** — 135 offline stdlib `unittest` tests.

Evidence: `pypa/sampleproject` → `ok`, 11 files, single-pass, ~16s, dates 2013→2024. Map-reduce confirmed with `--char-budget 4000` (4 chunks + 1 reduce). URL-spelling dedupe, embedding L2=1.0, and cascade delete all verified.

---

## Feature #2 — resume-tailor

### Done and verified (live end to end, compiled to PDF)

- **Retrieval** — stage 2B PostgreSQL hard-skill overlap filter (case-insensitive, scoped by `user_id`, 0.8 threshold with best-by-overlap fallback) + stage 2C in-memory cosine rank over the stored embeddings; top 3–4 projects with their `details_markdown`.
- **Generation** — two-pass Gemini chain (XYZ bullet generation + strict reviewer), prompts verbatim from the spec, bounded concurrency.
- **LaTeX** — pure injection into a base-only template, full special-char escaping, no empty itemize; output compiles with `pdflatex`.
- **Orchestrator** — `tailor_resume(job_specification, user_id, ...)` + CLI; JSON-serializable envelope degrading to `empty`/`error`.
- **Tests** — 108 offline stdlib `unittest` tests.

Evidence: a "Backend Engineer – API Platform" job against `demo-user`'s 4 seeded projects ranked **Requests** (HTTP Client Library) top by architecture cosine (0.790) over packaging (0.632) and markupsafe (0.594); reviewed bullets were first-person-free and metric-bearing; the `.tex` compiled to a 39 KB PDF.

---

## Feature #3 — reward-handler

### Done and verified (offline unit tests, schema, in-memory fallback, subagents)

- **Schema & Migrations** — `user_economy`, `verification_logs` (anti-cheat idempotency), `quest_completions`, `user_inventory` (badges, cosmetics, feature flags, verified architectures), and `weekly_leaderboard_snapshots`.
- **Verification Ingestion** — daily job applications check duplicate `confirmation_hash` (409 Conflict); awards 100 XP, 50 base bytes on first app of day, and tier bonus bytes multiplied by streak multiplier upon reaching daily target. Architectural quests award base 500 XP, 100 Cores, 200 Bytes, and +50 Cores high-quality bonus (score >= 90).
- **Streak State Machine** — daily midnight UTC rollover with milestone triggers (Day 7 bronze badge + 50 Cores, Day 30 gold badge + 250 Cores), active streak freeze consumption when target is missed, streak reset to 0 with zero freezes, and automatic lazy evaluation catch-up on user requests.
- **Shop Redemptions** — atomic debit using `SELECT ... FOR UPDATE` row-level locks on `user_economy` with inventory allocation for consumables, cosmetics, and functional platform flags (`unlocked_features`).
- **Redis Leaderboards** — pipelined atomic `ZINCRBY` updates across `leaderboard:all_time` and `leaderboard:weekly`, weekly reset snapshotting top 10 to Postgres, and an `InMemoryRedisLeaderboard` fallback for local dev when `REDIS_URL` is omitted.
- **Subagent Token Optimization** — 3-tier in-process funnel: Tier 0 commit-SHA cache + file filter (0 tokens) → Tier 1 Scout Subagent (`gemini-3.5-flash-lite`, ~800 tokens) → AST structural extractor (0 tokens) → Tier 2 Evaluator Subagent (`gemini-3.5-flash-lite`, ~1,500 tokens). Slashes evaluation cost from ~120,000 tokens to ~2,500 tokens (>97% reduction).
- **Tests** — 57 offline stdlib `unittest` tests.

Evidence: All 57 tests passing in <0.1s covering idempotency conflicts, streak multipliers, milestone payouts, freeze preservation, atomic shop transactions, Redis pipeline operations, and Scout/Evaluator subagents.

---

## What needs to be worked on

### Integration (the real next step, shared across all components)

- [ ] **FastAPI umbrella layer.** `analyze_repository`, `tailor_resume`, and `reward_handler`'s `create_fastapi_router` are all built to be called from HTTP routes and return JSON-serializable data, but nothing mounts them into an umbrella service yet.
- [ ] **Real `user_id` source.** The column and plumbing exist, but `user_id` is currently passed in; it needs to come from the WorkOS auth context once the HTTP layer exists.
- [ ] **Frontend wiring.** Components are not yet wired to `../frontend` (Next.js/WorkOS).

### Hardening / correctness

- [ ] **ivfflat index is created empty.** pgvector's ivfflat needs training data to be meaningful; at current row counts it does nothing. A `diskann` (pgvectorscale) upgrade is noted but unused. See `repo-analyzer/migrations/001_init.sql`.
- [ ] **No retry/caching on the tarball fetch** — only Gemini calls retry. See `repo-analyzer/ingestion.py::_fetch_repository_files_blocking`.
- [ ] **Private repos** work if the PAT has scope, but that path is untested.
- [ ] **Fabricated metrics in resume bullets.** The reviewer prompt (`ResumeTailor.md` §4) mandates a metric on every bullet, so the model invents plausible numbers. Inherent to the spec; consider grounding bullets in a `user_context`-style input of real figures. See `resume-tailor/generation.py`.
- [ ] **Free-tier Gemini quota** (250k input tokens/min for `gemini-3.5-flash-lite`) throttles large repos and multi-project tailoring; large repos (e.g. flask) hit 429. (Addressed in Feature #3 via tiered subagents).

### Housekeeping

- [ ] **Rotate the three credentials** (GitHub PAT, Gemini key, TigerCloud password) — pasted in plaintext in chat on 2026-09-12.
- [x] `user_id` on `projects` (migration 002).
- [x] Similarity-search read path — resume-tailor consumes the stored embeddings.
- [x] Specs tracked (`RepoAnalyzer.md`, `ResumeTailor.md`, `RewardHandler.md`); components documented in `CLAUDE.md` and per-component `DOCUMENTATION.md`.
