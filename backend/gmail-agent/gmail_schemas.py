"""Prompts and JSON response schemas for the Gmail agent's Gemini calls.

Two structured Gemini calls drive the pipeline:

1. Classification -- given recent email threads, keep only *job-application
   completion* emails (confirmations that the user successfully submitted /
   completed an application) and extract the company / role from each.
2. Matching -- given the qualifying emails and the user's open application rows
   (those without a ``thread_id`` yet), pick the closest matching row per email,
   or declare no match so a new row is created.

Both use ``common.gemini.generate_json`` (temperature 0, JSON schema output).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM_INSTRUCTION = (
    "You are an email triage assistant for a job-application tracker. You are "
    "given a batch of recent email threads. Identify ONLY the threads that are "
    "job-application *completion* emails: automated confirmations that the "
    "recipient has successfully submitted or completed a job application "
    "(e.g. 'Thank you for applying', 'We have received your application', "
    "'Your application was submitted', 'Application complete'). \n\n"
    "These are NOT completion emails and must be excluded: recruiter outreach or "
    "cold intros, interview invitations or scheduling, assessments/OA invites, "
    "rejections, offers, referral asks, newsletters, job alerts/recommendations, "
    "account or marketing mail, and anything not tied to a specific application "
    "the recipient submitted. When unsure, exclude it. \n\n"
    "For every thread in the input, return an entry keyed by its thread_id with "
    "is_completion set accordingly. For completion emails, extract the hiring "
    "company name and the role/title when present (empty string if absent), and "
    "a short status phrase quoted or paraphrased from the email "
    "(e.g. 'application received'). Respond ONLY with the JSON object."
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "is_completion": {"type": "boolean"},
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "status": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["thread_id", "is_completion"],
            },
        }
    },
    "required": ["results"],
}


def build_classify_prompt(threads: list[dict]) -> str:
    """Render the thread batch as a compact JSON payload for classification."""
    import json

    payload = [
        {
            "thread_id": t.get("thread_id", ""),
            "subject": t.get("subject", ""),
            "sender": t.get("sender", ""),
            "snippet": t.get("snippet", ""),
        }
        for t in threads
    ]
    return (
        "Classify each of the following email threads.\n"
        "THREADS (JSON):\n" + json.dumps(payload, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

MATCH_SYSTEM_INSTRUCTION = (
    "You match job-application confirmation emails to existing job-application "
    "records. You are given a list of EMAILS (each a completion email with a "
    "company/role) and a list of CANDIDATE application records (each an open row "
    "the user has not yet linked to an email, identified by a numeric index, "
    "with whatever company/role/metadata is known). \n\n"
    "For each email, choose the single candidate index whose company and role "
    "most clearly refer to the same application. Matching is primarily on company "
    "name (allow for legal-suffix and capitalization differences) and "
    "secondarily on role/title. If no candidate plausibly refers to the same "
    "application, return null for that email's match so a new record is created. "
    "Be conservative: a weak or company-only-coincidence match should be null. "
    "Respond ONLY with the JSON object."
)

MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "candidate_index": {"type": ["integer", "null"]},
                    "confidence": {"type": "number"},
                },
                "required": ["thread_id", "candidate_index"],
            },
        }
    },
    "required": ["matches"],
}


def build_match_prompt(emails: list[dict], candidates: list[dict]) -> str:
    """Render emails + candidate rows as a JSON payload for the matching call."""
    import json

    email_payload = [
        {
            "thread_id": e.get("thread_id", ""),
            "company": e.get("company", ""),
            "role": e.get("role", ""),
            "subject": e.get("subject", ""),
            "snippet": e.get("snippet", ""),
        }
        for e in emails
    ]
    candidate_payload = [
        {
            "index": c["index"],
            "company": c.get("company", ""),
            "role": c.get("role", ""),
            "metadata": c.get("metadata", {}),
        }
        for c in candidates
    ]
    return (
        "Match each email to a candidate record index, or null.\n"
        "EMAILS (JSON):\n" + json.dumps(email_payload, ensure_ascii=False) + "\n\n"
        "CANDIDATES (JSON):\n" + json.dumps(candidate_payload, ensure_ascii=False)
    )
