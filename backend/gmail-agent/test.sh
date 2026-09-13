cd backend/gmail-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
source .env

# Apply the schema fix once (the live-DB write I was blocked from doing):
psql "$POSTGRES_URL" -f migrations/003_job_applications_thread_id_text.sql

# Authorize Gmail (opens a browser consent window):
python gmail_agent.py auth

# Test a run:
python gmail_agent.py sync --hours 2
