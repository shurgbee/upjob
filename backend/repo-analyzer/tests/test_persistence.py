"""Unit tests for persistence module.

Tests the pure functions (normalize_dsn, ssl_argument, format_vector_literal,
parse_timestamp) without requiring asyncpg, database connection, or network access.

Run with: python3 -m unittest discover -s tests -t .
"""

import sys
import unittest
from datetime import datetime, timezone

# Add repo-analyzer to path so we can import persistence and schemas
sys.path.insert(0, "/Users/parthmodi/Desktop/Programming/Hackathons/upjob/backend/repo-analyzer")

# Import the module under test
from persistence import (
    format_vector_literal,
    normalize_dsn,
    parse_timestamp,
    ssl_argument,
)


class TestNormalizeDsn(unittest.TestCase):
    """Tests for normalize_dsn function."""

    def test_strips_sslmode_query_param(self):
        """sslmode query parameter is removed and returned separately."""
        dsn = "postgresql://user:pw@localhost:5432/db?sslmode=require"
        clean, sslmode = normalize_dsn(dsn)
        self.assertNotIn("sslmode", clean)
        self.assertEqual(sslmode, "require")

    def test_preserves_other_query_params(self):
        """Other query parameters are preserved."""
        dsn = "postgresql://user:pw@localhost:5432/db?sslmode=require&application_name=test&statement_timeout=30000"
        clean, sslmode = normalize_dsn(dsn)
        self.assertIn("application_name=test", clean)
        self.assertIn("statement_timeout=30000", clean)
        self.assertNotIn("sslmode", clean)
        self.assertEqual(sslmode, "require")

    def test_rewrites_postgres_scheme_to_postgresql(self):
        """postgres:// scheme is rewritten to postgresql://."""
        dsn = "postgres://user:pw@localhost:5432/db"
        clean, sslmode = normalize_dsn(dsn)
        self.assertTrue(clean.startswith("postgresql://"))
        self.assertIsNone(sslmode)

    def test_keeps_postgresql_scheme(self):
        """postgresql:// scheme is kept as-is."""
        dsn = "postgresql://user:pw@localhost:5432/db"
        clean, sslmode = normalize_dsn(dsn)
        self.assertTrue(clean.startswith("postgresql://"))

    def test_handles_dsn_without_query_string(self):
        """DSN with no query string returns None for sslmode."""
        dsn = "postgresql://user:pw@localhost:5432/db"
        clean, sslmode = normalize_dsn(dsn)
        self.assertIsNone(sslmode)
        self.assertEqual(clean, dsn)

    def test_handles_dsn_with_multiple_sslmode_values(self):
        """If sslmode appears multiple times, take the first value."""
        dsn = "postgresql://user:pw@localhost:5432/db?sslmode=require&sslmode=prefer"
        clean, sslmode = normalize_dsn(dsn)
        self.assertEqual(sslmode, "require")
        self.assertNotIn("sslmode", clean)

    def test_handles_empty_sslmode(self):
        """Empty sslmode query param is handled gracefully."""
        dsn = "postgresql://user:pw@localhost:5432/db?sslmode="
        clean, sslmode = normalize_dsn(dsn)
        self.assertEqual(sslmode, "")
        self.assertNotIn("sslmode", clean)

    def test_preserves_path_and_fragment(self):
        """Path and fragment are preserved."""
        dsn = "postgresql://user:pw@localhost:5432/db/schema?sslmode=require#anchor"
        clean, sslmode = normalize_dsn(dsn)
        self.assertIn("/db/schema", clean)
        self.assertEqual(sslmode, "require")


class TestSslArgument(unittest.TestCase):
    """Tests for ssl_argument function."""

    def test_disable_returns_false(self):
        """sslmode 'disable' maps to False."""
        self.assertFalse(ssl_argument("disable"))

    def test_allow_returns_prefer(self):
        """sslmode 'allow' maps to 'prefer'."""
        self.assertEqual(ssl_argument("allow"), "prefer")

    def test_prefer_returns_prefer(self):
        """sslmode 'prefer' maps to 'prefer'."""
        self.assertEqual(ssl_argument("prefer"), "prefer")

    def test_require_returns_require(self):
        """sslmode 'require' maps to 'require'."""
        self.assertEqual(ssl_argument("require"), "require")

    def test_verify_ca_returns_verify_ca(self):
        """sslmode 'verify-ca' maps to 'verify-ca'."""
        self.assertEqual(ssl_argument("verify-ca"), "verify-ca")

    def test_verify_full_returns_verify_full(self):
        """sslmode 'verify-full' maps to 'verify-full'."""
        self.assertEqual(ssl_argument("verify-full"), "verify-full")

    def test_none_returns_none(self):
        """sslmode None returns None."""
        self.assertIsNone(ssl_argument(None))

    def test_unknown_value_returns_prefer(self):
        """Unknown sslmode values default to 'prefer'."""
        self.assertEqual(ssl_argument("unknown"), "prefer")
        self.assertEqual(ssl_argument("invalid"), "prefer")


class TestFormatVectorLiteral(unittest.TestCase):
    """Tests for format_vector_literal function."""

    def test_formats_vector_correctly(self):
        """Vector is formatted as [v1,v2,v3,...] with no spaces."""
        vector = [0.1, 0.2, 0.3] + [0.0] * 765
        result = format_vector_literal(vector)
        self.assertTrue(result.startswith("[0.1,0.2,0.3,"))
        self.assertNotIn(" ", result)

    def test_handles_integers(self):
        """Integer values are accepted and formatted."""
        vector = [1, 2, 3] + [0.0] * 765
        result = format_vector_literal(vector)
        self.assertIn("[1.0,2.0,3.0,", result)

    def test_handles_negative_values(self):
        """Negative values are handled correctly."""
        vector = [-0.1, -0.2, -0.3] + [0.0] * 765
        result = format_vector_literal(vector)
        self.assertIn("[-0.1,-0.2,-0.3,", result)

    def test_raises_on_wrong_length(self):
        """ValueError is raised if vector length != EMBEDDING_DIMENSIONS."""
        too_short = [0.1, 0.2]
        with self.assertRaises(ValueError) as cm:
            format_vector_literal(too_short)
        self.assertIn("Expected 768 values", str(cm.exception))

    def test_raises_on_nan(self):
        """ValueError is raised for NaN values."""
        vector_with_nan = [float("nan")] + [0.0] * 767
        with self.assertRaises(ValueError) as cm:
            format_vector_literal(vector_with_nan)
        self.assertIn("not finite", str(cm.exception))

    def test_raises_on_inf(self):
        """ValueError is raised for infinite values."""
        vector_with_inf = [float("inf")] + [0.0] * 767
        with self.assertRaises(ValueError) as cm:
            format_vector_literal(vector_with_inf)
        self.assertIn("not finite", str(cm.exception))

    def test_raises_on_bool(self):
        """ValueError is raised for bool values."""
        vector_with_bool = [True] + [0.0] * 767
        with self.assertRaises(ValueError) as cm:
            format_vector_literal(vector_with_bool)
        self.assertIn("must be a finite real number", str(cm.exception))

    def test_raises_on_string(self):
        """ValueError is raised for string values."""
        vector_with_str = ["0.1"] + [0.0] * 767
        with self.assertRaises(ValueError) as cm:
            format_vector_literal(vector_with_str)
        self.assertIn("must be a finite real number", str(cm.exception))

    def test_raises_on_none(self):
        """ValueError is raised for None values."""
        vector_with_none = [None] + [0.0] * 767
        with self.assertRaises(ValueError) as cm:
            format_vector_literal(vector_with_none)
        self.assertIn("must be a finite real number", str(cm.exception))


class TestParseTimestamp(unittest.TestCase):
    """Tests for parse_timestamp function."""

    def test_returns_none_for_none(self):
        """parse_timestamp(None) returns None."""
        self.assertIsNone(parse_timestamp(None))

    def test_returns_none_for_empty_string(self):
        """parse_timestamp('') returns None."""
        self.assertIsNone(parse_timestamp(""))

    def test_returns_none_for_present_case_insensitive(self):
        """parse_timestamp('Present') (case-insensitive) returns None."""
        self.assertIsNone(parse_timestamp("Present"))
        self.assertIsNone(parse_timestamp("present"))
        self.assertIsNone(parse_timestamp("PRESENT"))
        self.assertIsNone(parse_timestamp("  PrEsEnT  "))

    def test_parses_iso_string_with_z_suffix(self):
        """ISO-8601 string with 'Z' suffix is parsed correctly."""
        result = parse_timestamp("2024-09-12T10:30:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 9)
        self.assertEqual(result.day, 12)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_parses_iso_string_with_offset(self):
        """ISO-8601 string with timezone offset is parsed correctly."""
        result = parse_timestamp("2024-09-12T10:30:00+05:30")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 9)
        self.assertEqual(result.day, 12)

    def test_parses_iso_string_with_negative_offset(self):
        """ISO-8601 string with negative timezone offset is parsed correctly."""
        result = parse_timestamp("2024-09-12T10:30:00-08:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)

    def test_parses_naive_iso_string_as_utc(self):
        """Naive ISO-8601 string (no timezone) is treated as UTC."""
        result = parse_timestamp("2024-09-12T10:30:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_returns_none_for_unparseable_string(self):
        """Unparseable strings return None."""
        self.assertIsNone(parse_timestamp("not a date"))
        self.assertIsNone(parse_timestamp("2024/09/12"))
        self.assertIsNone(parse_timestamp("garbage"))

    def test_returns_none_for_non_string_non_none(self):
        """Non-string, non-None values return None."""
        self.assertIsNone(parse_timestamp(12345))
        self.assertIsNone(parse_timestamp(3.14))
        self.assertIsNone(parse_timestamp([]))

    def test_handles_whitespace_around_iso_string(self):
        """Leading/trailing whitespace is stripped."""
        result = parse_timestamp("  2024-09-12T10:30:00Z  ")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)


class TestNoModuleLevelAsyncpgImport(unittest.TestCase):
    """Verify that asyncpg is not imported at module level."""

    def test_asyncpg_not_in_persistence_module(self):
        """asyncpg is not imported at the module level."""
        import persistence

        # asyncpg should not be in the module's namespace
        self.assertNotIn("asyncpg", dir(persistence))
        # Check the module dict directly
        self.assertNotIn("asyncpg", persistence.__dict__)


if __name__ == "__main__":
    unittest.main()
