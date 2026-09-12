"""Repository reference parsing and public-access validation helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote, urlparse


class RepositoryReferenceError(ValueError):
    """Raised when a GitHub repository reference is malformed."""


class PublicRepositoryRequired(ValueError):
    """Raised when a repository cannot be verified as public."""


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def parse(cls, value: str) -> RepositoryRef:
        raw = value.strip()
        if not raw:
            raise RepositoryReferenceError("Repository is required")

        if "://" in raw:
            parsed = urlparse(raw)
            if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
                raise RepositoryReferenceError(
                    "Repository URL must be an HTTPS URL on github.com"
                )
            parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        else:
            parts = raw.strip("/").split("/")

        if len(parts) != 2:
            raise RepositoryReferenceError(
                "Use owner/repository or https://github.com/owner/repository"
            )

        owner, name = parts
        name = name.removesuffix(".git")
        valid_part = re.compile(r"^[A-Za-z0-9_.-]+$")
        if not owner or not name or not valid_part.fullmatch(owner) or not valid_part.fullmatch(name):
            raise RepositoryReferenceError("Repository owner or name contains invalid characters")
        return cls(owner=owner, name=name)


class MCPToolCaller(Protocol):
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _to_plain_data(item) for key, item in vars(value).items()}
    return value


def _decode_embedded_json(value: Any) -> Any:
    """Decode JSON carried inside MCP text content while preserving other text."""

    if isinstance(value, str):
        try:
            return _decode_embedded_json(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return value
    if isinstance(value, list):
        return [_decode_embedded_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _decode_embedded_json(item) for key, item in value.items()}
    return value


def _repository_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "full_name" in value or "fullName" in value:
            records.append(value)
        for item in value.values():
            records.extend(_repository_records(item))
    elif isinstance(value, list):
        for item in value:
            records.extend(_repository_records(item))
    return records


def repository_is_public(result: Any, expected_full_name: str) -> bool:
    """Return true only for an exact search result explicitly marked non-private."""

    data = _decode_embedded_json(_to_plain_data(result))
    expected = expected_full_name.casefold()
    for record in _repository_records(data):
        full_name = str(record.get("full_name", record.get("fullName", ""))).casefold()
        private = record.get("private")
        visibility = str(record.get("visibility", "")).casefold()
        if full_name == expected and (private is False or visibility == "public"):
            return True
    return False


async def require_public_repository(server: MCPToolCaller, repo: RepositoryRef) -> None:
    """Verify visibility through GitHub MCP before any repository analysis."""

    result = await server.call_tool(
        "search_repositories",
        {
            "query": f"{repo.name} in:name user:{repo.owner}",
            "minimal_output": False,
            "perPage": 10,
        },
    )
    if not repository_is_public(result, repo.full_name):
        raise PublicRepositoryRequired(
            f"{repo.full_name} was not found as a public GitHub repository. "
            "Private repositories are not accepted."
        )
