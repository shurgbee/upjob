"""Unit tests for embeddings module.

Tests cover pure helpers without requiring google-genai or network calls.
"""

import sys
import unittest
from pathlib import Path

# Add the parent directory to sys.path so we can import embeddings
sys.path.insert(0, str(Path(__file__).parent.parent))

from embeddings import architecture_document, is_zero_vector


class TestArchitectureDocument(unittest.TestCase):
    """Tests for architecture_document."""

    def test_empty_list(self):
        """Empty input returns empty string."""
        result = architecture_document([])
        self.assertEqual(result, "")

    def test_single_architecture(self):
        """Single architecture string is returned as-is (with whitespace collapsed)."""
        result = architecture_document(["REST API"])
        self.assertEqual(result, "REST API")

    def test_multiple_architectures(self):
        """Multiple architectures joined with '; '."""
        result = architecture_document(["REST API", "Message Queue"])
        self.assertEqual(result, "REST API; Message Queue")

    def test_whitespace_collapsing(self):
        """Internal whitespace runs are collapsed to single spaces."""
        result = architecture_document(["REST  API", "Message\t\tQueue"])
        self.assertEqual(result, "REST API; Message Queue")

    def test_filters_non_strings(self):
        """Non-string items are filtered out."""
        result = architecture_document(["REST API", 123, "Message Queue", None])
        self.assertEqual(result, "REST API; Message Queue")

    def test_filters_blank_strings(self):
        """Blank strings and whitespace-only strings are filtered out."""
        result = architecture_document(["REST API", "", "  ", "Message Queue"])
        self.assertEqual(result, "REST API; Message Queue")

    def test_case_insensitive_dedupe_preserves_casing(self):
        """Duplicates differing only in case are deduplicated, preserving first casing."""
        result = architecture_document(
            ["REST API", "rest api", "REST API", "Message Queue"]
        )
        self.assertEqual(result, "REST API; Message Queue")

    def test_case_insensitive_dedupe_all_variations(self):
        """Mixed-case variations deduplicate to first-seen casing."""
        result = architecture_document(
            ["postgresql", "PostgreSQL", "POSTGRESQL", "MySQL"]
        )
        self.assertEqual(result, "postgresql; MySQL")

    def test_only_non_strings(self):
        """Input with only non-strings returns empty string."""
        result = architecture_document([123, None, True, []])
        self.assertEqual(result, "")

    def test_only_blank_strings(self):
        """Input with only blank strings returns empty string."""
        result = architecture_document(["", "  ", "\t\n"])
        self.assertEqual(result, "")

    def test_mixed_empty_and_valid(self):
        """Mix of empty and valid items filters correctly."""
        result = architecture_document(
            [
                None,
                "ETL Pipeline",
                "",
                "Reverse Proxy",
                123,
                "  ",
                "Message Queue",
            ]
        )
        self.assertEqual(result, "ETL Pipeline; Reverse Proxy; Message Queue")


class TestIsZeroVector(unittest.TestCase):
    """Tests for is_zero_vector."""

    def test_all_zeros_float(self):
        """Vector of all 0.0 returns True."""
        self.assertTrue(is_zero_vector([0.0, 0.0, 0.0]))

    def test_all_zeros_single(self):
        """Single element 0.0 returns True."""
        self.assertTrue(is_zero_vector([0.0]))

    def test_empty_sequence(self):
        """Empty sequence returns True (vacuous truth: all elements are 0.0)."""
        self.assertTrue(is_zero_vector([]))

    def test_nonzero_float(self):
        """Vector with any nonzero element returns False."""
        self.assertFalse(is_zero_vector([0.0, 0.1, 0.0]))

    def test_all_nonzero(self):
        """Vector of all nonzero returns False."""
        self.assertFalse(is_zero_vector([1.0, 2.0, 3.0]))

    def test_negative_zero(self):
        """Negative zero (-0.0) is exactly equal to 0.0 in Python."""
        self.assertTrue(is_zero_vector([0.0, -0.0, 0.0]))

    def test_small_nonzero(self):
        """Very small but nonzero value returns False."""
        self.assertFalse(is_zero_vector([0.0, 1e-10, 0.0]))


class TestImportWithoutGenai(unittest.TestCase):
    """Verify that embeddings can be imported without google-genai."""

    def test_import_succeeds(self):
        """Importing embeddings does not require google-genai."""
        # If google-genai were imported at module scope, this would fail.
        # We verify it's not in sys.modules after import (or at least, that
        # import itself succeeds).
        self.assertIn("embeddings", sys.modules)
        # Confirm google is not in sys.modules (the lazy import pattern works)
        self.assertNotIn("google.genai", sys.modules)


if __name__ == "__main__":
    unittest.main()
