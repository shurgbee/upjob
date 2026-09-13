"""Tiered in-process subagents for low-token architectural quest evaluations.

Optimizes token usage through a 3-tier funnel:
1. Tier 0 (Deterministic, 0 tokens): Cache lookup by commit SHA & heuristic tree filtering.
2. Tier 1 (Scout Subagent, ~800 tokens): Pinpoints 1-2 core files implementing the component.
3. Tier 2 (Evaluator Subagent, ~1,500 tokens): Strictly grades extracted code against a rubric.

Total tokens per evaluation ~2,500 vs ~120,000 in naive full-repo agent loops (>97% savings).
"""

from __future__ import annotations

import ast
import hashlib
import logging
import pathlib
import re
import sys
from typing import Any

# Bootstrap backend/ onto sys.path
_BACKEND_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from common.gemini import generate_json  # noqa: E402
from schemas import (  # noqa: E402
    DEFAULT_EVALUATOR_MODEL,
    DEFAULT_SCOUT_MODEL,
    EVALUATOR_SCHEMA,
    SCOUT_SCHEMA,
)

logger = logging.getLogger("reward_handler.subagents")

# High-noise patterns to ignore during discovery
IGNORED_PATH_PATTERNS: tuple[str, ...] = (
    "node_modules/",
    ".git/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    "__pycache__/",
    "tests/",
    "test/",
    ".github/",
    "docs/",
    ".idea/",
    ".vscode/",
)

IGNORED_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".lock",
    ".min.js", ".min.css", ".map", ".pdf", ".zip", ".tar", ".gz",
})

# In-memory evaluation cache: sha_key -> evaluation result dict
_EVALUATION_CACHE: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Tier 0: Deterministic Filtering & Caching (Zero Tokens)
# ---------------------------------------------------------------------------

def compute_cache_key(
    commit_sha_or_hash: str, architectural_component: str
) -> str:
    """Deterministic hash combining codebase identity and target component."""
    norm_comp = architectural_component.strip().lower()
    raw = f"{commit_sha_or_hash}:{norm_comp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_evaluation(cache_key: str) -> dict[str, Any] | None:
    return _EVALUATION_CACHE.get(cache_key)


def store_cached_evaluation(cache_key: str, result: dict[str, Any]) -> None:
    _EVALUATION_CACHE[cache_key] = result


def is_eligible_file(path: str) -> bool:
    """True if path is a meaningful source/manifest file (excluding noise)."""
    clean_path = path.replace("\\", "/").strip()
    if any(clean_path.startswith(prefix) or f"/{prefix}" in clean_path for prefix in IGNORED_PATH_PATTERNS):
        return False
    ext = pathlib.PurePath(clean_path).suffix.lower()
    if ext in IGNORED_EXTENSIONS:
        return False
    return True


def filter_file_tree(file_paths: list[str]) -> list[str]:
    """Filter and sort repository file paths by structural signal."""
    eligible = [p for p in file_paths if is_eligible_file(p)]
    # Keep up to 100 paths to prevent tree explosion
    return sorted(eligible)[:100]


def extract_python_structural_signatures(source_code: str, max_chars: int = 2500) -> str:
    """Extract class, method, and function signatures using AST to minimize token payload."""
    try:
        tree = ast.parse(source_code)
        signatures: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node) or ""
                doc_first = doc.split("\n")[0].strip() if doc else ""
                signatures.append(f"class {node.name}:  # {doc_first}")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [a.arg for a in sub.args.args]
                        fn_type = "async def" if isinstance(sub, ast.AsyncFunctionDef) else "def"
                        signatures.append(f"    {fn_type} {sub.name}({', '.join(args)})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                fn_type = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                signatures.append(f"{fn_type} {node.name}({', '.join(args)})")

        extracted = "\n".join(signatures)
        if len(extracted) > 100:
            return extracted[:max_chars]
    except Exception:  # noqa: BLE001
        pass

    # Fallback to lines with class/def/async or head of file
    lines = source_code.splitlines()
    key_lines = [
        line for line in lines
        if re.match(r"^\s*(class |def |async def |export |func |struct |impl )", line)
    ]
    if key_lines:
        return "\n".join(key_lines)[:max_chars]
    return source_code[:max_chars]


# ---------------------------------------------------------------------------
# Tier 1: Scout Subagent (Pinpoint Targeted Files)
# ---------------------------------------------------------------------------

SCOUT_SYSTEM_INSTRUCTION = """You are an architectural code scout.
Your mission is to find the 1 to 3 primary source code files in the given directory tree that implement the requested architectural component.
Be extremely selective: ignore general utilities, tests, setup configs, and docs.
Output strictly according to the JSON schema."""


async def run_scout_subagent(
    ai_client: Any,
    file_tree: list[str],
    architectural_component: str,
    readme_snippet: str = "",
    model: str = DEFAULT_SCOUT_MODEL,
) -> list[str]:
    """Scout subagent: given a directory tree, finds the 1-3 candidate files.

    Consumes ~800 tokens total.
    """
    if not file_tree or not ai_client:
        return []

    prompt = (
        f"Architectural Component to find: '{architectural_component}'\n\n"
        f"README Context:\n{readme_snippet[:800]}\n\n"
        f"Repository Files ({len(file_tree)}):\n"
        + "\n".join(f"- {p}" for p in file_tree[:80])
    )

    try:
        data = await generate_json(
            ai_client,
            model=model,
            prompt=prompt,
            schema=SCOUT_SCHEMA,
            system_instruction=SCOUT_SYSTEM_INSTRUCTION,
            max_output_tokens=256,
        )
        candidates = data.get("candidate_files", [])
        return [c for c in candidates if isinstance(c, str) and c in file_tree][:3]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scout subagent encountered error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Tier 2: Evaluator Subagent (Strict Rubric Scoring)
# ---------------------------------------------------------------------------

EVALUATOR_SYSTEM_INSTRUCTION = """You are a strict technical evaluator assessing whether a candidate's code implements a specified architectural component.
Grade strictly on evidence:
- Score >= 90: Outstanding production-grade implementation with clear concurrency, interfaces, separation of concerns, and robustness.
- Score 70-89: Solid working implementation fulfilling the core architectural requirement.
- Score < 70: Superficial, incomplete, broken, or trivial mock implementation.
Evaluate only the provided code snippet against the target component. Output strictly according to the JSON schema."""


async def run_evaluator_subagent(
    ai_client: Any,
    architectural_component: str,
    extracted_code_snippets: dict[str, str],
    model: str = DEFAULT_EVALUATOR_MODEL,
) -> dict[str, Any]:
    """Evaluator subagent: strictly scores the extracted code snippet.

    Consumes ~1,500 tokens total.
    """
    if not extracted_code_snippets or not ai_client:
        return {
            "score": 0,
            "passed": False,
            "key_mechanisms_found": [],
            "critique": "No relevant source code snippets found for evaluation.",
        }

    formatted_snippets = []
    for file_path, snippet in extracted_code_snippets.items():
        formatted_snippets.append(f"### File: {file_path}\n```\n{snippet}\n```")

    prompt = (
        f"Target Architectural Component: '{architectural_component}'\n\n"
        f"Code Evidence:\n\n" + "\n\n".join(formatted_snippets)
    )

    try:
        data = await generate_json(
            ai_client,
            model=model,
            prompt=prompt,
            schema=EVALUATOR_SCHEMA,
            system_instruction=EVALUATOR_SYSTEM_INSTRUCTION,
            max_output_tokens=512,
        )
        return {
            "score": max(0, min(100, int(data.get("score", 0)))),
            "passed": bool(data.get("passed", False)),
            "key_mechanisms_found": list(data.get("key_mechanisms_found", [])),
            "critique": str(data.get("critique", "")),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Evaluator subagent failed: %s", exc)
        return {
            "score": 0,
            "passed": False,
            "key_mechanisms_found": [],
            "critique": f"Evaluation model error: {exc}",
        }


# ---------------------------------------------------------------------------
# Integrated In-Process Evaluator Pipeline
# ---------------------------------------------------------------------------

async def evaluate_repository_component(
    repo_files: dict[str, str],
    architectural_component: str,
    commit_sha: str | None = None,
    ai_client: Any = None,
    scout_model: str = DEFAULT_SCOUT_MODEL,
    evaluator_model: str = DEFAULT_EVALUATOR_MODEL,
) -> dict[str, Any]:
    """End-to-end token-optimized evaluation pipeline.

    1. Cache check (0 tokens)
    2. File tree filter (0 tokens)
    3. Scout subagent (~800 tokens)
    4. Structural AST extraction (0 tokens)
    5. Evaluator subagent (~1,500 tokens)
    """
    # Tier 0: Check cache if commit SHA is present
    cache_key = None
    if commit_sha:
        cache_key = compute_cache_key(commit_sha, architectural_component)
        cached = get_cached_evaluation(cache_key)
        if cached:
            return {**cached, "cached": True}

    # Tier 0: Tree filter
    all_paths = list(repo_files.keys())
    eligible_paths = filter_file_tree(all_paths)

    if not eligible_paths:
        return {
            "score": 0,
            "passed": False,
            "key_mechanisms_found": [],
            "critique": "No eligible source code files found in repository.",
            "cached": False,
        }

    # Tier 1: Scout Subagent
    readme_content = repo_files.get("README.md", "") or repo_files.get("readme.md", "")
    target_files = await run_scout_subagent(
        ai_client,
        file_tree=eligible_paths,
        architectural_component=architectural_component,
        readme_snippet=readme_content,
        model=scout_model,
    )

    if not target_files:
        # Fallback to first 2 eligible code files
        target_files = eligible_paths[:2]

    # Deterministic Snippet Extraction
    snippets: dict[str, str] = {}
    for path in target_files:
        code = repo_files.get(path, "")
        if code:
            if path.endswith(".py"):
                snippets[path] = extract_python_structural_signatures(code)
            else:
                snippets[path] = code[:2500]

    # Tier 2: Evaluator Subagent
    eval_result = await run_evaluator_subagent(
        ai_client,
        architectural_component=architectural_component,
        extracted_code_snippets=snippets,
        model=evaluator_model,
    )

    eval_result["target_files_evaluated"] = target_files
    eval_result["cached"] = False

    # Store in cache if commit_sha was supplied
    if cache_key and eval_result.get("passed"):
        store_cached_evaluation(cache_key, eval_result)

    return eval_result
