from __future__ import annotations

import sys
import pathlib
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # resume-tailor
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))  # backend

from schemas import SelectedProject
import latex_resume
from latex_resume import (
    render_project_block,
    build_projects_section,
    load_template,
    render_resume,
    NAME_TOKEN,
    PROJECTS_TOKEN,
)


class TestRenderProjectBlock(unittest.TestCase):
    """Test render_project_block escaping and formatting."""

    def test_escapes_name_with_ampersand(self):
        """Project name with & is escaped to \\&."""
        project = SelectedProject(project_id=1, name="A & B", bullets=["test"])
        result = render_project_block(project)
        self.assertIn(r"\&", result)
        self.assertIn("A \\& B", result)

    def test_escapes_name_with_percent(self):
        """Project name with % is escaped to \\%."""
        project = SelectedProject(project_id=1, name="100% Done", bullets=["test"])
        result = render_project_block(project)
        self.assertIn(r"\%", result)
        self.assertIn("100\\% Done", result)

    def test_includes_item_lines(self):
        """Renders \\item lines for each bullet."""
        project = SelectedProject(
            project_id=1, name="Test Project", bullets=["first", "second"]
        )
        result = render_project_block(project)
        self.assertIn(r"\item first", result)
        self.assertIn(r"\item second", result)

    def test_empty_bullets_get_placeholder(self):
        """Project with empty bullets still renders subsection with placeholder."""
        project = SelectedProject(project_id=1, name="Empty", bullets=[])
        result = render_project_block(project)
        self.assertIn(r"\subsection*{Empty}", result)
        self.assertIn(r"\begin{itemize}", result)
        self.assertIn("(no bullets generated)", result)
        self.assertIn(r"\end{itemize}", result)
        # Ensure no empty itemize by checking structure
        self.assertNotIn(r"\begin{itemize}" + "\n" + r"\end{itemize}", result)

    def test_blank_bullets_filtered_placeholder_used(self):
        """Project with only blank bullets gets placeholder."""
        project = SelectedProject(project_id=1, name="Blank", bullets=["", "   ", None])
        result = render_project_block(project)
        self.assertIn("(no bullets generated)", result)
        self.assertIn(r"\begin{itemize}", result)
        self.assertIn(r"\end{itemize}", result)

    def test_has_subsection_and_itemize(self):
        """Renders \\subsection and itemize blocks."""
        project = SelectedProject(project_id=1, name="Test", bullets=["a", "b"])
        result = render_project_block(project)
        self.assertIn(r"\subsection*{Test}", result)
        self.assertIn(r"\begin{itemize}", result)
        self.assertIn(r"\end{itemize}", result)

    def test_escapes_bullet_with_percent(self):
        """Bullet containing % is escaped to \\%."""
        project = SelectedProject(
            project_id=1, name="Test", bullets=["Achieved 100% success"]
        )
        result = render_project_block(project)
        self.assertIn("100\\% success", result)


class TestBuildProjectsSection(unittest.TestCase):
    """Test build_projects_section joining and empty handling."""

    def test_empty_projects_returns_empty_string(self):
        """Empty projects list returns empty string."""
        result = build_projects_section([])
        self.assertEqual(result, "")

    def test_single_project(self):
        """Single project is rendered without extra separators."""
        project = SelectedProject(project_id=1, name="Single", bullets=["test"])
        result = build_projects_section([project])
        self.assertIn(r"\subsection*{Single}", result)
        # Should not have double newlines at start/end from joining
        self.assertFalse(result.startswith("\n\n"))
        self.assertFalse(result.endswith("\n\n"))

    def test_multiple_projects_joined_with_double_newline(self):
        """Multiple projects are joined with \\n\\n separator."""
        projects = [
            SelectedProject(project_id=1, name="First", bullets=["a"]),
            SelectedProject(project_id=2, name="Second", bullets=["b"]),
        ]
        result = build_projects_section(projects)
        self.assertIn("First", result)
        self.assertIn("Second", result)
        # Check that they are separated by double newline
        self.assertIn("\n\n", result)

    def test_three_projects(self):
        """Three projects are all included."""
        projects = [
            SelectedProject(project_id=1, name="A", bullets=["1"]),
            SelectedProject(project_id=2, name="B", bullets=["2"]),
            SelectedProject(project_id=3, name="C", bullets=["3"]),
        ]
        result = build_projects_section(projects)
        self.assertIn("\\subsection*{A}", result)
        self.assertIn("\\subsection*{B}", result)
        self.assertIn("\\subsection*{C}", result)


class TestLoadTemplate(unittest.TestCase):
    """Test load_template string vs file loading."""

    def test_string_template_returned_as_is(self):
        """Explicit template string is returned without reading file."""
        template_str = "Custom \\textbf{Template}"
        result = load_template(template_str)
        self.assertEqual(result, template_str)

    def test_none_template_loads_from_file(self):
        """None template loads from TEMPLATE_PATH."""
        result = load_template(None)
        # Check that it loads something and contains expected LaTeX tokens
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        # Should contain document class (from the actual template)
        self.assertIn(r"\documentclass", result)


class TestRenderResume(unittest.TestCase):
    """Test render_resume token replacement and escaping."""

    def test_replaces_candidate_name_token(self):
        """NAME_TOKEN is replaced with escaped candidate name."""
        projects = []
        result = render_resume(
            projects, candidate_name="John Doe", template=f"Name: {NAME_TOKEN}"
        )
        self.assertEqual(result, "Name: John Doe")

    def test_replaces_projects_token(self):
        """PROJECTS_TOKEN is replaced with projects section."""
        projects = [SelectedProject(project_id=1, name="Test", bullets=["item"])]
        result = render_resume(
            projects, candidate_name="Name", template=f"Projects:\n{PROJECTS_TOKEN}"
        )
        self.assertIn(r"\subsection*{Test}", result)
        self.assertNotIn(PROJECTS_TOKEN, result)

    def test_escapes_candidate_name_with_ampersand(self):
        """Candidate name with & is escaped."""
        projects = []
        result = render_resume(
            projects, candidate_name="A & B", template=f"Name: {NAME_TOKEN}"
        )
        self.assertEqual(result, "Name: A \\& B")

    def test_no_remaining_tokens_after_render(self):
        """Final output has no {{CANDIDATE_NAME}} or {{PROJECTS}} tokens."""
        projects = [SelectedProject(project_id=1, name="Test", bullets=["a"])]
        result = render_resume(
            projects,
            candidate_name="Test User",
            template=f"Name: {NAME_TOKEN}\nProjects:\n{PROJECTS_TOKEN}",
        )
        self.assertNotIn("{{CANDIDATE_NAME}}", result)
        self.assertNotIn("{{PROJECTS}}", result)

    def test_bullet_with_percent_escaped(self):
        """Bullet with 100% is rendered as 100\\%."""
        projects = [
            SelectedProject(project_id=1, name="Perf", bullets=["Improved 100% speed"])
        ]
        result = render_resume(
            projects, candidate_name="Name", template=f"Projects:\n{PROJECTS_TOKEN}"
        )
        self.assertIn("100\\% speed", result)
        self.assertNotIn("100% speed", result)  # Original unescaped should not be present

    def test_explicit_template_honored_over_file(self):
        """Passing an explicit template uses that, not the file."""
        projects = []
        custom = f"Custom: {NAME_TOKEN}"
        result = render_resume(
            projects, candidate_name="Test", template=custom
        )
        self.assertEqual(result, "Custom: Test")
        # If the file template were used, it would have \documentclass
        self.assertNotIn(r"\documentclass", result)

    def test_uses_file_template_when_none_given(self):
        """When template is None, loads from TEMPLATE_PATH."""
        projects = []
        result = render_resume(
            projects, candidate_name="Jane Smith", template=None
        )
        # Should contain expected LaTeX and our replacements
        self.assertIn("\\documentclass", result)
        self.assertIn("Jane Smith", result)
        self.assertNotIn(NAME_TOKEN, result)

    def test_uses_str_replace_not_regex_for_backslash_safety(self):
        """Backslash in replacement is literal, not re-interpreted as escape."""
        # If a project name contained a backslash, it should be escaped and
        # the result should have the escaped version, not a re-interpreted one.
        projects = [
            SelectedProject(project_id=1, name="Test\\Path", bullets=[])
        ]
        result = render_resume(
            projects, candidate_name="Name", template=f"{PROJECTS_TOKEN}"
        )
        # The backslash in "Test\Path" should be escaped to \textbackslash{}
        self.assertIn(r"\textbackslash{}", result)


class TestIntegration(unittest.TestCase):
    """Integration tests combining multiple functions."""

    def test_full_pipeline_with_multiple_projects(self):
        """Full pipeline: multiple projects, escaping, and template."""
        projects = [
            SelectedProject(
                project_id=1,
                name="Project A & B",
                bullets=["50% complete", "Uses C++ & Python"],
            ),
            SelectedProject(
                project_id=2,
                name="Project $Cost",
                bullets=[],
            ),
        ]
        template = f"Resume for {NAME_TOKEN}\n\n{PROJECTS_TOKEN}\n\nEnd"
        result = render_resume(
            projects, candidate_name="John & Jane", template=template
        )

        # Check all escaping is in place
        self.assertIn("John \\& Jane", result)
        self.assertIn("Project A \\& B", result)
        self.assertIn("50\\% complete", result)
        self.assertIn("Uses C++ \\& Python", result)  # + is not a LaTeX special char
        self.assertIn("Project \\$Cost", result)
        self.assertIn("(no bullets generated)", result)

        # No tokens remain
        self.assertNotIn(NAME_TOKEN, result)
        self.assertNotIn(PROJECTS_TOKEN, result)


if __name__ == "__main__":
    unittest.main()
