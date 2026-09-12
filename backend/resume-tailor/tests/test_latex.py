import sys
import pathlib
import unittest

# Add backend/ to path so we can import from common
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from common.latex import escape_latex, escape_bullets, render_bullet_items


class TestEscapeLatex(unittest.TestCase):
    """Tests for escape_latex function."""

    def test_backslash_escapes(self):
        """Test that backslash is escaped."""
        self.assertEqual(escape_latex("a\\b"), r"a\textbackslash{}b")

    def test_ampersand_escapes(self):
        """Test that ampersand is escaped."""
        self.assertEqual(escape_latex("a&b"), r"a\&b")

    def test_percent_escapes(self):
        """Test that percent is escaped."""
        self.assertEqual(escape_latex("100%"), r"100\%")

    def test_dollar_escapes(self):
        """Test that dollar is escaped."""
        self.assertEqual(escape_latex("$5"), r"\$5")

    def test_hash_escapes(self):
        """Test that hash is escaped."""
        self.assertEqual(escape_latex("#1"), r"\#1")

    def test_underscore_escapes(self):
        """Test that underscore is escaped."""
        self.assertEqual(escape_latex("x_y"), r"x\_y")

    def test_braces_escape(self):
        """Test that braces are escaped."""
        self.assertEqual(escape_latex("{x}"), r"\{x\}")

    def test_tilde_escapes(self):
        """Test that tilde is escaped."""
        self.assertEqual(escape_latex("a~b"), r"a\textasciitilde{}b")

    def test_caret_escapes(self):
        """Test that caret is escaped."""
        self.assertEqual(escape_latex("a^b"), r"a\textasciicircum{}b")

    def test_multiple_special_chars_no_double_escape(self):
        """Test that backslash + ampersand doesn't double-escape."""
        result = escape_latex("a\\b&c")
        expected = r"a\textbackslash{}b\&c"
        self.assertEqual(result, expected)

    def test_complex_string(self):
        """Test complex string with multiple special chars."""
        result = escape_latex("$5 & #1 {x}_y ~ ^")
        expected = r"\$5 \& \#1 \{x\}\_y \textasciitilde{} \textasciicircum{}"
        self.assertEqual(result, expected)

    def test_integer_coercion(self):
        """Test that non-string input is coerced via str()."""
        result = escape_latex(123)
        self.assertEqual(result, "123")

    def test_preserve_spaces(self):
        """Test that spaces are preserved."""
        result = escape_latex("a b c")
        self.assertEqual(result, "a b c")

    def test_normal_text_unchanged(self):
        """Test that normal text is unchanged."""
        result = escape_latex("Hello World")
        self.assertEqual(result, "Hello World")


class TestEscapeBullets(unittest.TestCase):
    """Tests for escape_bullets function."""

    def test_escapes_special_chars(self):
        """Test that bullets are escaped."""
        result = escape_bullets(["a&b", "c$d"])
        self.assertEqual(result, [r"a\&b", r"c\$d"])

    def test_drops_none(self):
        """Test that None items are dropped."""
        result = escape_bullets([None, "item"])
        self.assertEqual(result, ["item"])

    def test_drops_blank_after_strip(self):
        """Test that blank items are dropped."""
        result = escape_bullets(["", "   ", "item"])
        self.assertEqual(result, ["item"])

    def test_collapses_whitespace(self):
        """Test that internal whitespace is collapsed."""
        result = escape_bullets(["item  with   spaces"])
        self.assertEqual(result, ["item with spaces"])

    def test_empty_sequence(self):
        """Test that empty sequence returns empty list."""
        result = escape_bullets([])
        self.assertEqual(result, [])

    def test_mixed_input(self):
        """Test mixed valid and invalid bullets."""
        result = escape_bullets([None, "  item1  ", "", "item$2", "   "])
        self.assertEqual(result, ["item1", r"item\$2"])


class TestRenderBulletItems(unittest.TestCase):
    """Tests for render_bullet_items function."""

    def test_empty_sequence_returns_empty_string(self):
        """Test that empty sequence returns empty string."""
        result = render_bullet_items([])
        self.assertEqual(result, "")

    def test_single_item(self):
        """Test formatting of single item."""
        result = render_bullet_items(["item1"])
        self.assertEqual(result, r"    \item item1")

    def test_multiple_items(self):
        """Test formatting of multiple items."""
        result = render_bullet_items(["item1", "item2", "item3"])
        expected = r"    \item item1" + "\n" + r"    \item item2" + "\n" + r"    \item item3"
        self.assertEqual(result, expected)

    def test_preserves_escaped_content(self):
        """Test that pre-escaped content is preserved."""
        result = render_bullet_items([r"a\&b"])
        self.assertEqual(result, r"    \item a\&b")


if __name__ == "__main__":
    unittest.main()
