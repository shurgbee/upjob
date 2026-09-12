# Documentation — resume-tailor

How the feature works, how to navigate the code, and which file to open for a
given bug. For setup and commands see `README.md`; for status and the TODO list
see `../PROGRESS.md` (backend-wide); for the original spec see `../ResumeTailor.md`.

## What it does

Given a target **job specification** and a `user_id`, it builds a tailored LaTeX
resume from that user's already-analyzed projects (produced by `repo-analyzer`
and stored in PostgreSQL):

1. Retrieves the best-matching projects in two stages — a hard-skill **overlap
   filter** in SQL, then an **architecture cosine** rank over stored embeddings.
2. Generates and reviews Google-XYZ **bullet points** with a two-pass Gemini chain.
3. Injects the reviewed bullets into a static **LaTeX template** → `tailored_resume.tex`.

It reads the repo-analyzer tables; it never writes to `projects`. The candidate's
"DETAILS.md" is the `projects.details_markdown` column — no file I/O.

## File map

| File | Responsibility |
|---|---|
| `resume_tailor.py` | Public entry point `tailor_resume` + CLI. Orchestrates retrieve → generate → inject → write; owns the result envelope and error degradation. |
| `retrieval.py` | Two-stage retrieval: `OVERLAP_SQL` filter (stage 2B) + in-memory cosine rank (stage 2C). Pure helpers `parse_vector`, `cosine_similarity`, `select_candidates`, `rank_by_similarity`. |
| `generation.py` | Two-pass Gemini chain: `SYSTEM_PROMPT_GENERATE` / `SYSTEM_PROMPT_REVIEW` (verbatim from spec), prompt builders, `generate_all` with bounded concurrency. |
| `latex_resume.py` | Pure serializer: reviewed bullets → project blocks → template injection. No model, no DB. |
| `schemas.py` | The contract: `JobSpecification`, `SelectedProject`, `BULLETS_SCHEMA`, `build_job_specification`, `clean_bullets`. Pure, stdlib-only. |
| `templates/resume_template.tex` | Base-only LaTeX template with `{{CANDIDATE_NAME}}` and `{{PROJECTS}}` tokens. |
| `tests/` | Offline stdlib `unittest` — one module per source file. |
| `../common/` | Shared `db.connect`, `gemini.generate_json`, `embeddings.embed_text`, `latex.escape_latex`, `models.*`. |

## Data flow

```
tailor_resume(job_specification, user_id, ...)         [resume_tailor.py]
  │
  ├─ build_job_specification(payload) → JobSpecification [schemas.py]
  ├─ resolve GEMINI_API_KEY + DATABASE_URL (env or args)
  │
  ├─ connect(dsn)                                        [common/db.py]
  └─ select_projects(conn, job, user_id, api_key=...)    [retrieval.py]
  │     embed_job_architecture → embed_text (in memory)  [common/embeddings.py]
  │     fetch_overlap_candidates (OVERLAP_SQL, top 10)   ← stage 2B, scoped by user_id
  │     select_candidates (0.8 threshold + fallback)
  │     rank_by_similarity (cosine = dot of normalized)  ← stage 2C, top 3–4
  │   → [SelectedProject]  (with details_markdown)
  │
  ├─ generate_all(job, selected, api_key=...)            [generation.py]
  │     per project, bounded-concurrent:
  │       generate_bullets  (SYSTEM_PROMPT_GENERATE) ─┐   pass 1
  │       review_bullets    (SYSTEM_PROMPT_REVIEW)  ←─┘   pass 2
  │   → projects with .bullets set
  │
  ├─ render_resume(selected, candidate_name=...)         [latex_resume.py]
  │     escape_latex / escape_bullets                    [common/latex.py]
  │     str.replace {{CANDIDATE_NAME}} and {{PROJECTS}}
  │
  └─ write tailored_resume.tex
  → JSON envelope {status, selected_projects, output_path, tex_chars, ...}
```

Returns a JSON-serializable envelope with identical keys on success and failure;
`status` is `ok` / `empty` (no match) / `error`.

## Key invariants (do not break these)

- **Retrieval embeds with the EMBEDDING model, not the generation model.** The
  job-architecture vector must come from `gemini-embedding-001` (768-dim,
  L2-normalized) to be comparable to the stored vectors. Never pass the
  generation model into `select_projects` — leave it unset so it falls back to
  `EMBEDDING_MODEL`. (This was a live bug: a generation model → 404 on `embedContent`.)
- **Cosine == dot product** here because both vectors are already L2-normalized.
- **Overlap filter has a fallback.** Real projects rarely cover 80% of a job's
  tech list, so when fewer than `min_candidates` clear the threshold,
  `select_candidates` falls back to best-by-overlap — the pipeline must still
  return candidates.
- **The LaTeX template comment must not contain the literal `{{PROJECTS}}` /
  `{{CANDIDATE_NAME}}` tokens**, or `str.replace` injects the block twice.
  Injection uses plain `str.replace` (not regex) so replacement backslashes are
  never reinterpreted.
- **All user/model text is LaTeX-escaped** before injection; never emit an empty
  `itemize` (LaTeX errors on it).
- **Degrade, don't abort.** Any stage failure returns the envelope with
  `status="error"` and the job title/candidate populated, never raises.
- **Heavy SDKs are lazy.** `asyncpg` and `google-genai` are reached only through
  `common.db` / `common.gemini` / `common.embeddings`, which import them inside
  functions, so the 108-test suite runs with neither installed.

## Which file to change for a bug

| Symptom | Start here |
|---|---|
| Job JSON rejected or fields dropped | `schemas.py` — `build_job_specification` (accepts capitalised + lowercase keys) |
| No projects selected / wrong projects / threshold too strict | `retrieval.py` — `OVERLAP_SQL`, `select_candidates` (threshold + fallback), `DEFAULT_*` |
| Similarity ranking looks wrong / all scores ~0 | `retrieval.py` — `parse_vector`, `cosine_similarity`, `rank_by_similarity`; check embeddings are 768-dim normalized |
| "model not found for embedContent" / 404 on retrieval | `resume_tailor.py` — ensure the generation `model` is NOT forwarded to `select_projects`; `common/models.py` — `EMBEDDING_MODEL` |
| Bullets weak, first-person, or missing metrics | `generation.py` — `SYSTEM_PROMPT_GENERATE` / `SYSTEM_PROMPT_REVIEW`, `build_generation_prompt` |
| Fabricated metrics | `generation.py` — the reviewer prompt forces a metric; change the prompt or add grounding (see `../PROGRESS.md`) |
| Transient 429/5xx during generation | `../common/gemini.py` — `is_retryable_error`, `MAX_ATTEMPTS`, backoff |
| Project bullets not set / concurrency issues | `generation.py` — `tailor_project`, `generate_all` |
| `.tex` malformed, double-injected, or won't compile | `latex_resume.py` — `render_resume`, `build_projects_section`; `templates/resume_template.tex` |
| Special characters break LaTeX | `../common/latex.py` — `escape_latex`, `escape_bullets`, `render_bullet_items` |
| DB connection / SSL errors | `../common/db.py` — `connect`, `normalize_dsn`, `ssl_argument` |
| Envelope shape / CLI flags / file not written | `resume_tailor.py` — `tailor_resume`, `_build_parser`, `main` |

## Testing

All tests are offline stdlib `unittest`:

```bash
python -m unittest discover -s tests -t .
```

Conventions to preserve when adding tests:
- Stub the Gemini client with a fake exposing `.models.generate_content` (async)
  returning `.parsed` — see `tests/test_generation.py`.
- Use a fake async connection returning canned dict rows for retrieval — see
  `tests/test_retrieval.py`; never hit a real database.
- Test the orchestrator by monkeypatching `retrieval.select_projects`,
  `generation.generate_all`, and `resume_tailor.connect` — see
  `tests/test_resume_tailor.py`.

For a true end-to-end check you need `GEMINI_API_KEY` and `DATABASE_URL` in
`.env`, projects already in the DB for the `user_id` (seed them with
`repo-analyzer`), and optionally a LaTeX engine (`pdflatex`) to compile the
result:

```bash
python resume_tailor.py job.json --user-id <id> --candidate-name "Jane Doe" \
    --output tailored_resume.tex
```
