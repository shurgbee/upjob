# RepoAnalyzer

Ingest a public GitHub repository URL plus optional user context, and produce a structured project specification, a DETAILS.md skill document, and a pgvector embedding of the project's architectures.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in the required keys (see Environment variables below). The `.env` file is gitignored and must never be committed.

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `GITHUB_PAT` | GitHub personal access token; raises API rate limit from 60 to 5,000 requests/hour and allows access to private repos you own. | `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `GEMINI_API_KEY` | Google Gemini API key for model calls. | `AIzaSyDxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |
| `DATABASE_URL` | PostgreSQL connection string with pgvector extension installed. TigerCloud and Neon work out of the box. | `postgresql://user:pass@host:5432/dbname` |

## Database

Apply migrations before the first run:

```bash
psql $DATABASE_URL < migrations/001_init.sql
```

This creates the `projects` and `project_architectures` tables with the pgvector extension enabled.

## Tests

Run the test suite offline with:

```bash
python3 -m unittest discover -s tests -t .
```

The project uses stdlib `unittest`, not pytest. The suite is fully offline — no network, no database, and no model calls.

## How It Works

The pipeline has five stages:

1. **Parse the repo URL** into owner, repo, and ref (defaulting to `main`).

2. **Download the repository** as a single tarball snapshot (one API request for the whole tree, rather than one request per file) and filter while streaming: skip `node_modules`, `.git`, `venv`/`.venv`, `target`, `dist`, `build`, `vendor`, `__pycache__`, `.next`, binary and asset extensions, lockfiles, minified bundles, and oversized files. A character budget caps total ingested bytes.

3. **Resolve timestamps** deterministically from the GitHub commits API: the earliest and latest commit timestamps are fetched using the `Link: rel="last"` header (two requests total). The model is never asked to compute dates.

4. **Analyze with Gemini**. Small repositories take a single long-context call. Repositories exceeding the character budget are chunked along module boundaries, each chunk analyzed in parallel into a JSON fragment with a shared global header (path tree, README, manifest) so no chunk is read blind. Fragments are merged by deterministic set-union in Python, then one final reduce call writes the summary and collapses near-duplicate names.

5. **Persist** the project specification to the `projects` table (arrays as native `TEXT[]`), render DETAILS.md via a pure serializer, and store a `text-embedding-004` vector of the architectures in `project_architectures` keyed by `project_id`.

## Limits

- Only one snapshot commit is analyzed; full repository history is not scanned.
- Repositories exceeding the ingestion character budget are rejected entirely rather than silently half-analyzed.
- If truncation ever occurs (unlikely with the current budget), omitted paths are reported in the result.
