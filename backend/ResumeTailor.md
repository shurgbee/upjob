### Specification: Feature #2 – Semantic Resume Tailor

#### Overview & Boundaries

- **Purpose:** Dynamically generate a tailored LaTeX resume by matching a candidate's existing projects against a specific job description using PostgreSQL filtering and vector similarity, then utilizing a two-pass LLM chain to generate and refine Google XYZ formatted bullet points.
- **Execution Model:** Two-stage retrieval (PostgreSQL filtering + Vector Search) followed by a two-pass LLM generation and evaluation chain using `gemini-3.5-flash-lite`.
- **Required Inputs:**
- `job_specification` (JSON): The extracted requirements of the target job.
- `project_specifications` (JSON Array): The candidate's existing project portfolio.

- **Outputs:**
- `tailored_resume.tex`: A compiled LaTeX document containing the selected projects and their tailored, reviewed bullet points.

#### 1. Data Structures & Terminology

Adhere strictly to the taxonomy defined in the HackRice project to separate raw tools from tangible deliverables:

- **Hard Skills / Technologies:** Raw tools and languages utilized, such as Python, cgroups v2, or AWS.

- **Architectural Components:** Tangible systems or mechanisms actually built, such as a Distributed Container Engine, Reverse Proxy, or ETL Pipeline.

- **Core Competencies:** Overarching applied methodologies, such as process isolation or scaling microservices.

**Job Specification JSON format:**

```json
{
  "Title": "string",
  "Url": "string",
  "Technologies": ["string (Hard skills like Python, AWS)"],
  "Architecture": ["string (Architectural components like ETL Pipeline)"],
  "YOE": "integer",
  "Publish_Date": "string (ISO-8601)",
  "Spec_Created_At": "string (ISO-8601)",
  "Spec_Updated_At": "string (ISO-8601)"
}
```

#### 2. Retrieval & Semantic Matching Pipeline

- **Step 2A (Embedding):** Generate a vector embedding for the Job Specification's `Architecture` array using `gemini-embedding-1`. Do not save this embedding to the vector store; hold it in memory for the search.
- **Step 2B (Hard Skill Filter):** Query the PostgreSQL database to retrieve the top 10 projects where the candidate's `Technologies` array has at least an 80% overlap with the job's required `Technologies`.
- **Step 2C (Vector Similarity Search):** Run a cosine similarity search comparing the in-memory Job `Architecture` embedding against the `Architectural Components` embeddings of the 10 filtered projects.
- **Selection:** Return the top 3 to 4 projects with the highest similarity scores and fetch their corresponding `DETAILS.md` documents.

#### 3. LLM Bullet Point Generation (Pass 1)

Pass the Target Job Specification and the selected `DETAILS.md` contexts to `gemini-3.5-flash-lite`.

- **System Prompt:**
  > "You are an expert technical resume writer. Generate 3 bullet points for each provided project using the Google XYZ format: 'Accomplished [X] as measured by [Y] by doing [Z]'. Start each bullet with a strong action verb, include a clear metric, and detail the specific action or skill used. Output strictly as a JSON array of strings."

#### 4. LLM Bullet Point Evaluation (Pass 2)

Pass the generated bullet points back into `gemini-3.5-flash-lite` for a rigid quality-assurance check.

- **System Prompt:**
  > "You are a strict resume reviewer. Review the provided resume bullets and correct the following common failures: 1) Remove all first-person pronouns (I, me, my). 2) Replace weak verbs like 'helped', 'worked on', or 'assisted' with strong action verbs. 3) Remove unnecessary articles to maximize conciseness. 4) Ensure every single bullet contains a quantifiable metric. Output the corrected bullets strictly as a JSON array of strings."

#### 5. Final Output Compilation (LaTeX Injection)

- Map the finalized, reviewed bullet points back to their respective Project Titles.
- To ensure stability, write a Python script that injects these mapped JSON strings into a static `.tex` resume template (escaping special characters like `%`, `&`, and `$`).
- Output the final `tailored_resume.tex` file.
