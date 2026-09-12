"""
Safely escape LaTeX special characters to prevent injection and breakage.

This module provides pure, deterministic functions to escape user and model text
for safe inclusion in LaTeX documents. Escaped text renders literally without
breaking LaTeX syntax or enabling injection attacks.
"""

from typing import Sequence


def escape_latex(text: str) -> str:
    """
    Escape every LaTeX special character so the input renders literally.

    Replaces special characters in an order that does not double-escape:
    backslash, ampersand, percent, dollar, hash, underscore, braces, tilde, caret.

    Args:
        text: The text to escape (coerced to str if not already).

    Returns:
        The escaped string with all LaTeX special characters replaced.
    """
    text = str(text)

    # Character-by-character replacement to avoid double-escaping.
    # Process each character exactly once.
    result = []
    for char in text:
        if char == '\\':
            result.append(r'\textbackslash{}')
        elif char == '&':
            result.append(r'\&')
        elif char == '%':
            result.append(r'\%')
        elif char == '$':
            result.append(r'\$')
        elif char == '#':
            result.append(r'\#')
        elif char == '_':
            result.append(r'\_')
        elif char == '{':
            result.append(r'\{')
        elif char == '}':
            result.append(r'\}')
        elif char == '~':
            result.append(r'\textasciitilde{}')
        elif char == '^':
            result.append(r'\textasciicircum{}')
        else:
            result.append(char)

    return ''.join(result)


def escape_bullets(bullets: Sequence[str]) -> list[str]:
    """
    Apply escape_latex to each bullet item and filter blanks.

    Drops items that are None or blank after stripping, and collapses
    internal whitespace runs to single spaces.

    Args:
        bullets: Sequence of bullet strings.

    Returns:
        List of escaped, non-blank bullet strings.
    """
    escaped = []
    for bullet in bullets:
        if bullet is None:
            continue
        # Collapse internal whitespace to single spaces
        text = ' '.join(str(bullet).split())
        # Skip blanks after stripping
        if text:
            escaped.append(escape_latex(text))
    return escaped


def render_bullet_items(bullets: Sequence[str]) -> str:
    """
    Return LaTeX list items, one per line.

    Each item is formatted as "    \\item " + bullet text, joined by newlines.

    Args:
        bullets: Sequence of bullet strings (typically already escaped).

    Returns:
        LaTeX item lines, or empty string if no bullets.
    """
    if not bullets:
        return ""

    items = [f"    \\item {bullet}" for bullet in bullets]
    return "\n".join(items)
