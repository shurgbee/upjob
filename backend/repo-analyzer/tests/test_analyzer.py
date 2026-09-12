from types import SimpleNamespace

import pytest
from google.genai import types
from mcp import types as mcp_types

from repo_analyzer.analyzer import RepositoryAnalysisError, RepositoryAnalyzer
from repo_analyzer.config import Settings
from repo_analyzer.models import ResumeAnalysis
from repo_analyzer.repository import RepositoryRef


def github_tool(name: str) -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description=f"Test {name}",
        inputSchema={"type": "object", "properties": {}},
    )


class FakeSession:
    def __init__(self, tools: list[mcp_types.Tool]) -> None:
        self.tools = tools
        self.calls = []

    async def list_tools(self):
        return mcp_types.ListToolsResult(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(text='{"name":"demo"}')]
        )


@pytest.mark.asyncio
async def test_gemini_tool_declarations_include_only_analysis_tools() -> None:
    session = FakeSession(
        [
            github_tool("search_repositories"),
            github_tool("get_file_contents"),
            github_tool("search_code"),
            github_tool("list_commits"),
        ]
    )
    tools = await RepositoryAnalyzer._gemini_tools(session)
    names = {item.name for item in tools[0].function_declarations}
    assert names == {"get_file_contents", "search_code", "list_commits"}


@pytest.mark.asyncio
async def test_missing_mcp_tool_is_reported() -> None:
    session = FakeSession([github_tool("get_file_contents")])
    with pytest.raises(RepositoryAnalysisError, match="list_commits, search_code"):
        await RepositoryAnalyzer._gemini_tools(session)


@pytest.mark.asyncio
async def test_evidence_loop_executes_scoped_gemini_tool_call() -> None:
    tool_response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_function_call(
                            name="get_file_contents",
                            args={"owner": "acme", "repo": "widget", "path": "README.md"},
                        )
                    ],
                )
            )
        ]
    )
    notes_response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part(text="Evidence from README.md")]
                )
            )
        ]
    )

    class FakeModels:
        def __init__(self) -> None:
            self.responses = [tool_response, notes_response]
            self.requests = []

        async def generate_content(self, **kwargs):
            self.requests.append(kwargs)
            return self.responses.pop(0)

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    session = FakeSession(
        [
            github_tool("get_file_contents"),
            github_tool("search_code"),
            github_tool("list_commits"),
        ]
    )
    events = []
    analyzer = RepositoryAnalyzer(
        Settings("gemini-test", "github-test"), conversation_callback=events.append
    )

    notes = await analyzer._collect_evidence(
        client=client,
        session=session,
        repo=RepositoryRef("acme", "widget"),
        instructions="Inspect evidence.",
        request="Analyze acme/widget.",
    )

    assert notes == "Evidence from README.md"
    assert session.calls == [
        ("get_file_contents", {"owner": "acme", "repo": "widget", "path": "README.md"})
    ]
    assert len(models.requests) == 2
    assert [event["event"] for event in events] == [
        "tool_call",
        "tool_result",
        "gemini_message",
    ]
    assert events[0]["tool"] == "get_file_contents"


@pytest.mark.asyncio
async def test_structured_output_uses_json_schema_field() -> None:
    output = ResumeAnalysis(
        Summary="A test project.",
        Architectures=[],
        Technologies=[],
        Actions=[],
        Metrics=[],
    )
    response = types.GenerateContentResponse(parsed=output)

    class FakeModels:
        def __init__(self) -> None:
            self.request = None

        async def generate_content(self, **kwargs):
            self.request = kwargs
            return response

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    analyzer = RepositoryAnalyzer(Settings("gemini-test", "github-test"))

    result = await analyzer._structure_output(
        client=client,
        instructions="Return structured output.",
        request="Analyze acme/widget.",
        evidence="README.md describes a test project.",
        output_type=ResumeAnalysis,
    )

    config = models.request["config"]
    assert result == output
    assert config.response_schema is None
    assert config.response_json_schema == ResumeAnalysis.model_json_schema()
    assert config.response_json_schema["additionalProperties"] is False
