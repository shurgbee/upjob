from types import SimpleNamespace

import pytest

from repo_analyzer.analyzer import tool_call_targets_repository
from repo_analyzer.repository import (
    PublicRepositoryRequired,
    RepositoryRef,
    RepositoryReferenceError,
    repository_is_public,
    require_public_repository,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("openai/openai-python", "openai/openai-python"),
        ("https://github.com/openai/openai-python", "openai/openai-python"),
        ("https://www.github.com/openai/openai-python.git/", "openai/openai-python"),
    ],
)
def test_parse_repository(value: str, expected: str) -> None:
    assert RepositoryRef.parse(value).full_name == expected


@pytest.mark.parametrize(
    "value",
    ["", "openai", "git@github.com:openai/openai-python.git", "https://gitlab.com/a/b", "a/b/c"],
)
def test_rejects_invalid_repository(value: str) -> None:
    with pytest.raises(RepositoryReferenceError):
        RepositoryRef.parse(value)


def test_public_repository_from_structured_result() -> None:
    result = SimpleNamespace(
        structuredContent={
            "items": [
                {"full_name": "openai/openai-python", "private": False},
            ]
        },
        content=[],
    )
    assert repository_is_public(result, "OpenAI/openai-python")


def test_private_repository_is_rejected() -> None:
    result = {"items": [{"full_name": "acme/internal", "private": True}]}
    assert not repository_is_public(result, "acme/internal")


def test_decodes_json_text_mcp_content() -> None:
    result = {
        "content": [
            {
                "type": "text",
                "text": '{"items":[{"full_name":"acme/public","visibility":"public"}]}',
            }
        ]
    }
    assert repository_is_public(result, "acme/public")


@pytest.mark.asyncio
async def test_public_check_uses_scoped_search() -> None:
    class Server:
        def __init__(self) -> None:
            self.arguments = None

        async def call_tool(self, tool_name, arguments):
            assert tool_name == "search_repositories"
            self.arguments = arguments
            return {"items": [{"full_name": "acme/widget", "private": False}]}

    server = Server()
    await require_public_repository(server, RepositoryRef("acme", "widget"))
    assert server.arguments["query"] == "widget in:name user:acme"


@pytest.mark.asyncio
async def test_public_check_rejects_no_exact_match() -> None:
    class Server:
        async def call_tool(self, tool_name, arguments):
            return {"items": [{"full_name": "acme/widget-fork", "private": False}]}

    with pytest.raises(PublicRepositoryRequired):
        await require_public_repository(Server(), RepositoryRef("acme", "widget"))


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        ("get_file_contents", {"owner": "acme", "repo": "widget", "path": "src"}, True),
        ("list_commits", {"owner": "ACME", "repo": "Widget"}, True),
        ("get_file_contents", {"owner": "acme", "repo": "private"}, False),
        ("search_code", {"query": "TODO repo:acme/widget"}, True),
        ("search_code", {"query": "TODO"}, False),
        ("search_code", {"query": "repo:acme/widget OR repo:other/repo"}, False),
        ("search_repositories", {"query": "widget"}, False),
    ],
)
def test_tool_call_repository_scope(tool_name, arguments, expected) -> None:
    repo = RepositoryRef("acme", "widget")
    assert tool_call_targets_repository(tool_name, arguments, repo) is expected
