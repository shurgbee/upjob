# Public GitHub Repository Analyzer

A framework-independent Python service that uses the official GitHub MCP server and Google Gemini
to:

1. evaluate repository evidence against a learning objective; or
2. extract evidence-backed material for project resume bullets.

Only public repositories are accepted. Before the model reads the repository, the service calls
GitHub MCP's `search_repositories` tool and requires an exact result explicitly marked public. The
MCP server is restricted to four read-only tools. After visibility validation, the agent sees only
file contents, code search, and commit listing. Application-side validation rejects reads that do
not target the exact validated repository.

## Setup

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Set these secrets in `.env`:

- `GEMINI_API_KEY`: used by the Google Gen AI SDK.
- `GITHUB_PAT`: sent as a bearer token to the official remote GitHub MCP server. For public-only
  analysis, use the fine-grained, read-only token described below.

`.env` is ignored by Git. The default MCP endpoint is
`https://api.githubcopilot.com/mcp/`; it is configured as read-only and limited to repository
inspection tools.

### Curating the GitHub token

Prefer a **fine-grained personal access token**, not a classic token. GitHub lets fine-grained
tokens be limited by resource owner, repository, permission, and expiration; classic tokens are
broader and may expose every repository your account can access.

1. Open GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens** and
   select **Generate new token**.
2. Name it `public-repo-analyzer` and add a short description identifying this application.
3. Set a short expiration, such as 30 days. Rotate it before expiry instead of creating an
   unlimited token.
4. Choose your own account as the resource owner. Do not choose an organization unless its public
   repositories specifically require that owner selection.
5. Under repository access, do not grant access to private repositories. Fine-grained tokens always
   include read-only access to public repositories. If GitHub requires a repository selection,
   choose only the public repository or repositories you intend to analyze.
6. Under repository permissions, grant only:
   - **Contents: Read-only** — needed for files, code search, and commits.
   - **Metadata: Read-only** — normally selected automatically and used to identify repositories.
7. Leave every organization permission and account permission at **No access**. Grant no `write` or
   `admin` permission.
8. Generate the token, copy it once, and store it only as `GITHUB_PAT` in `.env`. Never commit it,
   print it, put it in a URL, or send it to Gemini.

The application adds `X-MCP-Readonly: true`, exposes only four GitHub MCP read tools, checks that
the target is public before analysis, and validates every Gemini-generated MCP call against the
target repository. Those controls are defense in depth; the token should still be least-privilege.

For a long-running multi-user service, replace the developer PAT with a GitHub App or per-user
OAuth flow so users do not share one developer credential.

## Usage

Evaluate a learning objective:

```bash
repo-analyzer skill \
  --repo https://github.com/owner/repository \
  --objective "Build a REST API with authentication, persistence, and automated tests"
```

Extract resume material:

```bash
repo-analyzer resume --repo owner/repository
```

Stream the live Gemini/MCP conversation while preserving clean JSON output:

```bash
repo-analyzer resume --repo owner/repository --show-conversation
```

Conversation events are emitted immediately as timestamped JSON Lines on stderr. They include the
request, repository validation, visible Gemini messages, MCP tool arguments and results, and the
final structured response. Internal Gemini thought data and credentials are never emitted. The
final schema remains the only output on stdout, so it can still be piped to `jq` or another process:

```bash
repo-analyzer skill \
  --repo owner/repository \
  --objective "Build a tested REST API" \
  --show-conversation \
  > result.json 2> conversation.jsonl
```

Both commands write only the requested JSON object to stdout. Validation and configuration errors
are written as `{"error": "..."}` to stderr and use exit code 2.

You can also call the async service directly; this is the seam intended for a future FastAPI route:

```python
from repo_analyzer import RepositoryAnalyzer
from repo_analyzer.config import Settings

analyzer = RepositoryAnalyzer(Settings.from_env())
skill_result = await analyzer.evaluate_skill(
    "owner/repository",
    "Understand event-driven architecture and reliable message processing",
)
resume_result = await analyzer.extract_resume_material("owner/repository")
```

## Output contracts

Skill evaluation:

```json
{
  "Passed_all_criteria": false,
  "Score": 75,
  "Passed_criteria": ["..."],
  "Failed_criteria": ["..."],
  "Actionable_Feedback": "..."
}
```

Resume material:

```json
{
  "Summary": "...",
  "Architectures": ["..."],
  "Technologies": ["..."],
  "Actions": ["..."],
  "Metrics": ["..."]
}
```

The schemas are strict Pydantic models. Metrics must be explicitly evidenced in the repository;
the agent returns an empty list instead of inventing numbers.

## Tests

```bash
pytest
ruff check .
```

The unit tests do not call Gemini or GitHub. A real end-to-end run requires both credentials and
network access.
