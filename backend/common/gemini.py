"""Gemini API helpers: JSON generation with retries.

Provides common utilities for calling the Gemini API with structured output,
including retry logic for transient failures.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

#: Flash capacity is shared and bursty: a 503 or 429 is routine and says nothing
#: about the request. Retrying matters more in map-phase calls, where one unlucky
#: chunk would otherwise discard every other chunk's completed work.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
RETRY_BASE_DELAY = 2.0


def is_retryable_error(exc: BaseException) -> bool:
    """True for transient API failures worth another attempt.

    The SDK exposes ``code`` on its API errors; when it does not, fall back to
    matching the status in the message, since a capacity error must not be
    mistaken for a bad request and retried forever.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code in RETRYABLE_STATUS_CODES
    message = str(exc)
    return any(str(status) in message for status in RETRYABLE_STATUS_CODES)


async def generate_json(
    ai_client: Any,
    *,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    system_instruction: str,
    use_response_schema: bool = False,
    max_output_tokens: int = 8192,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    """One structured Gemini call returning a parsed JSON object.

    Mirrors the response handling in ``simplify-scraper``: prefer ``parsed``,
    fall back to parsing ``text``, and fail loudly rather than returning a
    half-understood shape. Transient failures are retried with exponential
    backoff and jitter; anything else is raised immediately.

    Args:
        ai_client: A genai.Client.aio instance
        model: Model name
        prompt: Prompt text
        schema: Structured-output schema
        system_instruction: System instruction text
        use_response_schema: Send the schema through the SDK's compatibility
            ``response_schema`` field instead of ``response_json_schema``.
            Some Gemini models reject full JSON Schema keywords such as
            ``additionalProperties``.
        max_output_tokens: Token limit (default 8192)
        max_attempts: Maximum retry attempts (default MAX_ATTEMPTS)

    Returns:
        Parsed JSON object from the response

    Raises:
        Exception: Non-retryable errors from the API
        RuntimeError: Empty response or malformed JSON
    """
    schema_field = "response_schema" if use_response_schema else "response_json_schema"
    config = {
        "system_instruction": system_instruction,
        "response_mime_type": "application/json",
        schema_field: schema,
        "temperature": 0,
        "max_output_tokens": max_output_tokens,
    }
    for attempt in range(1, max_attempts + 1):
        try:
            response = await ai_client.models.generate_content(
                model=model, contents=prompt, config=config
            )
            break
        except Exception as exc:  # noqa: BLE001 - re-raised unless transient
            if attempt >= max_attempts or not is_retryable_error(exc):
                raise
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            await asyncio.sleep(delay + random.uniform(0, delay / 2))

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response")
    loaded = json.loads(text)
    if not isinstance(loaded, dict):
        raise RuntimeError("Gemini did not return a JSON object")
    return loaded
