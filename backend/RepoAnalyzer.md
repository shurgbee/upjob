Here is the updated, production-ready specification formatted so Claude Code can execute and implement the feature cleanly without ambiguity or guesswork.

---

## Specification: Feature #1 – GitHub Repository Ingestion & Skill Profiling

### Overview & Boundaries

- **Purpose:** Ingest a public or user-owned GitHub repository URL along with optional user context, analyze the codebase structure and history via GitHub MCP tools using an agent loop, extract structured architectural metadata, and persist both structured project specifications and a generated `DETAILS.md` skill document.
- **Execution Model:** LangChain agent loop utilizing Gemini with GitHub MCP tools, followed by structured extraction using native schema validation.
- **Required Inputs:**
- `github_repo_url` (String, required): Valid GitHub repository URL.
- `user_context` (Object, optional): Supplementary data containing user-supplied metrics (numbers processed, throughput), project impact (business/technical goals), and architectural challenges solved.

- **Outputs:**
- PostgreSQL `projects` table record.
- Rendered `DETAILS.md` document stored alongside the project record.
- Vector store embeddings of `architectures` linked via foreign key.

---

### Step-by-Step Architecture Pipeline

#### 1. GitHub MCP Tool Initialization

- Connect to the official GitHub MCP server via standard transport (e.g., `stdio` subprocess or remote endpoint).
- Inject an authenticated GitHub Personal Access Token (PAT) into the MCP environment to elevate API rate limits from the unauthenticated 60 requests/hour to 5,000 requests/hour.
- Bind the core MCP read tools to the agent:
- Directory tree / file listing tools (for structure inspection).
- `get_file_contents` (for reading manifests and docs).

- `list_commits` (for commit history and dates).

#### 2. Discovery & Selective Extraction Loop (Agent Phase)

- The agent inspects the repository file tree at the root and up to two subfolder levels.
- **Hard Exclusion Filters:** Ignore high-noise/binary directories (`node_modules`, `.git`, `venv`, `target`, `dist`, `build`, vendor directories).
- **Targeted File Read Limit:** Restrict `get_file_contents` to a strict maximum of 4 files:
- `README.md` (or equivalent top-level documentation).
- Primary package/dependency manifest (`package.json`, `requirements.txt`, `go.mod`, `Cargo.toml`, or `pom.xml`).
- Infrastructure/Architecture configuration (`Dockerfile`, `docker-compose.yml`, Kubernetes manifests, or GitHub Actions CI/CD workflows).

- **Deterministic Date Retrieval:** Call `list_commits` to fetch:
- Earliest commit timestamp (`start_time`).
- Most recent commit timestamp (`end_time`).

#### 3. Agent System Prompt & Schema Synthesis

- **System Prompt:**
  > You are an automated repository analysis agent. Analyze the provided GitHub repository using your GitHub MCP tools.
  > Step 1: Discovery. Inspect the repository tree to identify key files. Ignore build/package folders.
  > Step 2: Read Files. Read the README.md and up to 3 core manifest or infrastructure configuration files.
  > Step 3: Get Dates. Use commit tools to find the earliest and latest commit timestamps.
  > Step 4: Synthesize. Extract and categorize Hard Skills (tools/languages), Core Competencies (methodologies), and Architectural Components (concrete systems built). Adhere strictly to the required structured output schema.

#### 4. Unified Structured Output Schema

Enforce a single-pass structured schema containing two nested objects to avoid running multiple LLM passes:

- **Object 1: `project_specification**`
- `name` (String): Project name.
- `description` (String): Concise project summary.
- `start_time` (String): ISO-8601 date of earliest commit.
- `end_time` (String): ISO-8601 date of most recent commit (or 'Present').
- `technologies` (String Array): Raw tools, libraries, databases, and frameworks used.

- `architectures` (String Array): Tangible systems built (e.g., "ETL Pipeline", "Distributed Container Engine", "Reverse Proxy").

- **Object 2: `details` (For DETAILS.md generation)**
- `Summary` (String): 1–2 sentence overview of the project's engineering purpose.
- `Architectural_Components` (String Array): Distinct functional systems and subsystems built.

- `Core_Competencies` (String Array): Applied methodologies (e.g., "Process Isolation", "Stream Processing").

- `Technologies` (String Array): Specific tools, cloud resources, and environments utilized.

- `Actions` (String Array): Action-driven statements detailing technical implementation steps and solutions.
- `Metrics` (String Array): Quantifiable numbers, throughput, latency improvements, or test coverage figures.

---

### Data Persistence & Downstream Artifacts

#### 1. PostgreSQL Persistence (`projects` table)

- Save the parsed `project_specification` fields into PostgreSQL.
- Store array fields (`technologies`, `architectures`) as native PostgreSQL `TEXT[]` or `JSONB` to allow performant relational querying and indexing.
- Set timestamps for `spec_created_at` and `spec_updated_at`.

#### 2. DETAILS.md Generation

- Serialize the `details` JSON object into a standard Markdown file.
- Map each JSON key directly into a `##` Markdown subheading (replacing underscores with spaces).
- Format each array item as an unordered Markdown bullet point (`*`).

#### 3. Vector Store Embeddings

- Extract the `architectures` list from the `project_specification`.
- Generate vector embeddings for the combined architecture strings using an embedding model (e.g., `text-embedding-004`).
- Insert the vector record into the vector store (e.g., `pgvector` or local vector collection), storing the PostgreSQL `project_id` as the foreign key/payload metadata for subsequent similarity matching.
