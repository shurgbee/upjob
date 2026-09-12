# Simplify internship scraper

This script reads the active internship tables in the SimplifyJobs Summer 2027
README, identifies new application URLs, and opens each posting with
CloakBrowser. The rendered HTML is cleaned of scripts and styling, then sent to
Gemini with a structured-output schema matching `sample.json`.

## Install and run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m cloakbrowser install
printf 'GEMINI_API_KEY=your-key\n' > .env
python simplify_scraper.py --max-jobs 10
```

The first stateful run considers the feed's `0d` rows new. Later runs only
process application URLs absent from `.simplify_scraper_state.json`. If
`--max-jobs` limits a batch, the remaining new URLs stay queued in that state
file for the next run. Useful options:

```bash
# First run: include jobs up to two days old
python simplify_scraper.py --first-run-age-days 2

# Stateless preview of at most five currently-new rows
python simplify_scraper.py --no-state --max-jobs 5

# Scrape every active listing and save the result
python simplify_scraper.py --all --output jobs.json

# Some protected sites work better with a visible browser and residential proxy
python simplify_scraper.py --headful --proxy http://user:pass@host:port

# Override the model or HTML input ceiling
python simplify_scraper.py --gemini-model gemini-2.5-flash --max-html-chars 750000
```

The command prints a JSON object with `generated_at`, `source_url`,
`new_posting_count`, and `postings`. Each posting has exactly the fields defined
by `sample.json`; its `id` is a UUID v4. An individual browser or Gemini failure
is returned in the same shape with `scrape_status: "error"`, and does not abort
the batch.

## FastAPI integration

`scrape_new_jobs` is async and returns only JSON-serializable values:

```python
from fastapi import FastAPI
from simplify_scraper import scrape_new_jobs

app = FastAPI()

@app.get("/jobs/new")
async def new_jobs():
    return await scrape_new_jobs(max_jobs=25)
```

For production, protect the route from overlapping runs and move browser work
to a task queue if requests must return quickly. Keep `GEMINI_API_KEY` in your
deployment's secret store. Store the state file on a persistent volume, or
replace `_load_state`/`_save_state` with a database-backed repository.
