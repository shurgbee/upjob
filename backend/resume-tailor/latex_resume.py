from __future__ import annotations

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # resume-tailor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # backend

from typing import Sequence

from schemas import SelectedProject
from common.latex import escape_latex, escape_bullets, render_bullet_items

__all__ = [
    "TEMPLATE_PATH",
    "NAME_TOKEN",
    "PROJECTS_TOKEN",
    "render_project_block",
    "build_projects_section",
    "load_template",
    "render_resume",
]

#: Path to the static LaTeX resume template.
TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent / "templates" / "resume_template.tex"

#: Token placeholder for candidate name in the template.
NAME_TOKEN = "{{CANDIDATE_NAME}}"

#: Token placeholder for projects section in the template.
PROJECTS_TOKEN = "{{PROJECTS}}"


def render_project_block(project: SelectedProject) -> str:
    """Render one project as a LaTeX subsection with bullet items.

    Escapes the project name with escape_latex and bullets with escape_bullets
    and render_bullet_items. If there are no non-blank bullets after escaping,
    still renders the subsection with a single placeholder item "(no bullets generated)"
    to avoid LaTeX errors from an empty itemize environment.

    Uses plain LaTeX list format: \\subsection*{name} followed by itemize.

    Args:
        project: SelectedProject with name and bullets.

    Returns:
        LaTeX block string (no trailing newline).
    """
    escaped_name = escape_latex(project.name)
    escaped_bullets = escape_bullets(project.bullets)

    block = f"\\subsection*{{{escaped_name}}}\n\\begin{{itemize}}\n"

    if escaped_bullets:
        # Render actual bullet items.
        items = render_bullet_items(escaped_bullets)
        block += items + "\n"
    else:
        # No bullets generated: add a placeholder to avoid empty itemize.
        # LaTeX errors if \\begin{itemize} has no \\item.
        block += "    \\item (no bullets generated)\n"

    block += "\\end{itemize}"

    return block


def build_projects_section(projects: Sequence[SelectedProject]) -> str:
    """Join rendered project blocks with newlines.

    Args:
        projects: Sequence of SelectedProject instances.

    Returns:
        Concatenated LaTeX blocks for all projects, separated by "\n\n".
        Returns "" for empty input.
    """
    if not projects:
        return ""
    blocks = [render_project_block(p) for p in projects]
    return "\n\n".join(blocks)


def load_template(template: str | None = None) -> str:
    """Load a LaTeX template string.

    Args:
        template: If a string, return it as-is. Otherwise, read from TEMPLATE_PATH.

    Returns:
        The template content.
    """
    if isinstance(template, str):
        return template
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def render_resume(
    projects: Sequence[SelectedProject],
    *,
    candidate_name: str,
    template: str | None = None,
) -> str:
    """Render a complete LaTeX resume by injecting projects and candidate name.

    Replaces NAME_TOKEN and PROJECTS_TOKEN in the template. Uses str.replace (plain,
    not regex) to avoid backslash reinterpretation: a literal backslash in the
    replacement text is never re-interpreted as an escape sequence.

    Args:
        projects: Sequence of SelectedProject instances for the projects section.
        candidate_name: Name to inject into the NAME_TOKEN placeholder.
        template: Optional template string; if None, load from TEMPLATE_PATH.

    Returns:
        Complete LaTeX document with tokens replaced. Must contain no remaining
        "{{...}}" tokens for NAME or PROJECTS.
    """
    tmpl = load_template(template)
    escaped_name = escape_latex(candidate_name)
    projects_text = build_projects_section(projects)

    # Use plain str.replace to avoid backslash reinterpretation in the replacement.
    result = tmpl.replace(NAME_TOKEN, escaped_name)
    result = result.replace(PROJECTS_TOKEN, projects_text)

    return result
