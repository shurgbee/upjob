# Resume Tailor

Resume Tailor is a pipeline that takes a job specification and a user's portfolio of analyzed projects, retrieves the best-matching projects by hard-skill overlap and architecture vector similarity, generates and reviews resume bullets for each project using a two-pass Gemini chain, and injects the bullets into a LaTeX resume template.

## Setup

### Prerequisites

- Python 3.11 or later
- PostgreSQL database with pgvector extension
- Gemini API key

### Installation

1. Create a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables:

   Copy `.env.example` to `.env` and fill in the required values:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and add:
   - `GEMINI_API_KEY`: Your Google GenAI API key
   - `DATABASE_URL`: PostgreSQL connection string (e.g., `postgresql://user:password@localhost/dbname`)

   **Note:** `.env` is in `.gitignore` and should never be committed. Use `.env.example` as a template with placeholder values only.

## Usage

Run the resume tailor from the command line:

```bash
python resume_tailor.py job.json \
  --user-id <user_id> \
  --candidate-name "Jane Doe" \
  --output tailored_resume.tex
```

### Arguments

- `job_spec`: Job specification as inline JSON or path to a JSON file
  - Must include `Title` (required) and may include `Url`, `Technologies`, `Architecture`, `YOE`, `Publish_Date`
  - See [ResumeTailor.md](../../../docs/ResumeTailor.md) section 1 for the full schema

### Options

- `--user-id`: User ID to scope project retrieval (optional)
- `--candidate-name`: Name to inject into the resume template (default: "Candidate")
- `--output`: Path to write the tailored resume LaTeX file (default: `tailored_resume.tex`)
- `--model`: Gemini model to use (default: `gemini-2.0-flash-exp`)
- `--top-k`: Number of projects to select (default: 4)
- `--threshold`: Overlap ratio threshold for hard-skill filtering (default: 0.8)
- `--json-out`: Optional path to write the result envelope as JSON

### Output

The command returns a JSON envelope with the following structure:

```json
{
  "status": "ok" | "empty" | "error",
  "error": null | "error message",
  "job_title": "Target Job Title",
  "candidate_name": "Jane Doe",
  "user_id": "user_123",
  "output_path": "/path/to/tailored_resume.tex",
  "selected_projects": [
    {
      "project_id": 1,
      "name": "Project Name",
      "technologies": ["Python", "PostgreSQL"],
      "architectures": ["ETL Pipeline"],
      "overlap_ratio": 0.8,
      "similarity": 0.92,
      "bullets": ["Generated bullet 1", "Generated bullet 2", "Generated bullet 3"]
    }
  ],
  "tex_chars": 2048
}
```

## How It Works

The resume tailor runs through five stages:

1. **Embed Job Architecture**: The target job's architecture tags are embedded using the Gemini embedding model (768 dimensions) to prepare for vector similarity ranking.

2. **Hard-Skill Overlap Filter** (PostgreSQL): A SQL query fetches projects from the database, computing the ratio of job technologies found in each project's technology list. Projects are ordered by overlap ratio descending.

3. **Threshold Filter with Fallback**: If at least `min_candidates` projects meet the overlap threshold, they are selected. Otherwise, all projects are included to ensure the pipeline always has candidates.

4. **Vector Similarity Ranking**: Each candidate's project architecture embedding is compared to the job's embedded architecture using cosine similarity. The top-k projects by similarity are selected.

5. **Two-Pass Bullet Generation and Review**:
   - **Pass 1 (Generation)**: For each project, Gemini generates three resume bullets in the Google XYZ format: "Accomplished [X] as measured by [Y] by doing [Z]."
   - **Pass 2 (Review)**: The bullets are reviewed and corrected for first-person pronouns, weak verbs, article usage, and metric inclusion.
   - **LaTeX Injection**: The reviewed bullets are injected into a LaTeX resume template and written to disk (if `--output` is specified).

## Prerequisites

- **Projects must already be in the database** via the repo-analyzer pipeline. Each project must have:
  - A record in the `projects` table with `name`, `technologies`, and `user_id`
  - A corresponding row in the `project_architectures` table with an embedding (768-dim pgvector)

- **Embeddings must use the same model** as the job embedding:
  - Both use `gemini-embedding-001` (768 dimensions)
  - Embeddings are L2-normalized, so cosine similarity equals the dot product

## Limitations

- Generates a LaTeX `.tex` file; does **not** compile to PDF
- Requires a LaTeX toolchain (e.g., pdflatex, xelatex) to produce a PDF from the generated `.tex` file
- Generated bullets are limited to the context window of Gemini 2.0 Flash Exp

## Testing

Run the offline test suite:

```bash
python3 -m unittest discover -s tests -t .
```

All tests are fully offline:
- No database calls
- No network requests
- No Gemini API calls
- All external dependencies are monkeypatched

### Test Coverage

- Happy path: Successful retrieval, generation, and LaTeX injection with file written
- Empty case: No projects match the selection criteria
- Missing API key or database URL: Error handling
- Invalid job specification: Error handling
- Helper functions: `_load_job_spec`, `_build_parser`, argument parsing
