import sys
import os
import unittest

# Add repo-analyzer directory to sys.path if needed
repo_analyzer_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_analyzer_dir not in sys.path:
    sys.path.insert(0, repo_analyzer_dir)

from details_md import render_details_md
from schemas import DETAILS_LIST_KEYS, DETAILS_SECTION_ORDER, DETAILS_SUMMARY_KEY


class TestRenderDetailsMd(unittest.TestCase):
    """Test suite for render_details_md function."""

    def test_underscore_to_space_in_headings(self):
        """Underscores in section keys are replaced with spaces in headings."""
        details = {
            DETAILS_SUMMARY_KEY: "Test",
            "Architectural_Components": [],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("## Architectural Components", result)
        self.assertIn("## Core Competencies", result)
        self.assertIn("## Technologies", result)
        self.assertIn("## Actions", result)
        self.assertIn("## Metrics", result)

    def test_all_six_sections_present_and_in_order(self):
        """All sections from DETAILS_SECTION_ORDER are present in order."""
        details = {
            DETAILS_SUMMARY_KEY: "Test summary",
            "Architectural_Components": ["Component 1"],
            "Core_Competencies": ["Competency 1"],
            "Technologies": ["Tech 1"],
            "Actions": ["Action 1"],
            "Metrics": ["Metric 1"],
        }
        result = render_details_md(details)

        positions = {}
        for key in DETAILS_SECTION_ORDER:
            heading = key.replace("_", " ")
            pos = result.find(f"## {heading}")
            self.assertGreaterEqual(pos, 0, f"Section '{heading}' not found")
            positions[key] = pos

        # Verify order
        keys_list = list(DETAILS_SECTION_ORDER)
        for i in range(len(keys_list) - 1):
            self.assertLess(
                positions[keys_list[i]],
                positions[keys_list[i + 1]],
                f"{keys_list[i]} should appear before {keys_list[i + 1]}",
            )

    def test_bullet_formatting_for_lists(self):
        """List items are formatted as bullets with '* ' prefix."""
        details = {
            DETAILS_SUMMARY_KEY: "",
            "Architectural_Components": ["Component 1", "Component 2"],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("* Component 1", result)
        self.assertIn("* Component 2", result)

    def test_title_h1_present_when_provided(self):
        """H1 title is added when title parameter is provided."""
        details = {
            k: [] if k != DETAILS_SUMMARY_KEY else "" for k in DETAILS_SECTION_ORDER
        }
        result = render_details_md(details, title="My Project")
        lines = result.split("\n")
        self.assertEqual(lines[0], "# My Project")

    def test_title_h1_absent_when_not_provided(self):
        """No H1 title when title parameter is None or empty."""
        details = {
            k: [] if k != DETAILS_SUMMARY_KEY else "" for k in DETAILS_SECTION_ORDER
        }
        result = render_details_md(details, title=None)
        self.assertFalse(result.startswith("# "))

        result = render_details_md(details, title="")
        self.assertFalse(result.startswith("# "))

        result = render_details_md(details, title="   ")
        self.assertFalse(result.startswith("# "))

    def test_none_identified_placeholder(self):
        """_None identified._ is used for empty list sections."""
        details = {
            DETAILS_SUMMARY_KEY: "Test",
            "Architectural_Components": [],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        # Should appear multiple times (once per empty list section)
        self.assertGreater(result.count("_None identified._"), 0)

    def test_no_summary_available_placeholder(self):
        """_No summary available._ is used when Summary is missing or empty."""
        details = {
            DETAILS_SUMMARY_KEY: "",
            "Architectural_Components": [],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("_No summary available._", result)

        # Test with missing Summary key
        details_no_key = {
            "Architectural_Components": [],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details_no_key)
        self.assertIn("_No summary available._", result)

    def test_non_string_items_filtered_out(self):
        """Non-string items in lists are filtered out."""
        details = {
            DETAILS_SUMMARY_KEY: "Test",
            "Architectural_Components": ["Valid", 123, None, True, ["nested"]],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("* Valid", result)
        self.assertNotIn("* 123", result)
        self.assertNotIn("* None", result)
        self.assertNotIn("* True", result)

    def test_blank_items_filtered_out(self):
        """Blank items (after stripping) are filtered out."""
        details = {
            DETAILS_SUMMARY_KEY: "Test",
            "Architectural_Components": ["Valid", "", "  ", "\t\n"],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("* Valid", result)
        lines = result.split("\n")
        bullet_lines = [l for l in lines if l.startswith("*")]
        self.assertEqual(len(bullet_lines), 1)

    def test_multiline_summary_collapsed(self):
        """Multiline summary is collapsed to single line."""
        details = {
            DETAILS_SUMMARY_KEY: "This is a\nmulti-line\nsummary with  extra   spaces",
            "Architectural_Components": [],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("This is a multi-line summary with extra spaces", result)

    def test_multiline_list_item_collapsed(self):
        """Multiline list items are collapsed to single lines."""
        details = {
            DETAILS_SUMMARY_KEY: "",
            "Architectural_Components": ["Multi\nline\nitem with  spaces"],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        self.assertIn("* Multi line item with spaces", result)

    def test_none_input_returns_valid_document(self):
        """Passing None as details returns valid document without raising."""
        result = render_details_md(None)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        # Should have placeholders for empty sections
        self.assertIn("_No summary available._", result)
        self.assertIn("_None identified._", result)

    def test_exactly_one_trailing_newline(self):
        """Result ends with exactly one trailing newline."""
        details = {
            k: [] if k != DETAILS_SUMMARY_KEY else "test"
            for k in DETAILS_SECTION_ORDER
        }
        result = render_details_md(details)
        # Should end with exactly one newline
        self.assertTrue(result.endswith("\n"))
        self.assertFalse(result.endswith("\n\n"))

    def test_no_trailing_whitespace_on_lines(self):
        """No line has trailing whitespace."""
        details = {
            DETAILS_SUMMARY_KEY: "Test",
            "Architectural_Components": ["Item 1", "Item 2"],
            "Core_Competencies": [],
            "Technologies": [],
            "Actions": [],
            "Metrics": [],
        }
        result = render_details_md(details)
        for line in result.rstrip("\n").split("\n"):
            self.assertFalse(
                line.endswith(" "), f"Line has trailing space: {repr(line)}"
            )
            self.assertFalse(
                line.endswith("\t"), f"Line has trailing tab: {repr(line)}"
            )

    def test_no_double_blank_lines(self):
        """Result contains no more than one consecutive blank line."""
        details = {
            k: [] if k != DETAILS_SUMMARY_KEY else "test"
            for k in DETAILS_SECTION_ORDER
        }
        result = render_details_md(details)
        self.assertNotIn("\n\n\n", result)


if __name__ == "__main__":
    unittest.main()
