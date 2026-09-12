"""Pure serializer for rendering repository details to Markdown.

This module renders details dicts (from Gemini extraction) to DETAILS.md format
with no model involvement, ensuring the output is fully deterministic and
reproducible regardless of input shape or completeness.
"""

from schemas import DETAILS_SECTION_ORDER, DETAILS_SUMMARY_KEY


def render_details_md(details: dict, *, title: str | None = None) -> str:
    """Render a details dict to DETAILS.md Markdown format.

    Args:
        details: A dict with keys from DETAILS_SECTION_ORDER. Missing keys,
                 None values, wrong types, and malformed input are all handled
                 gracefully without raising.
        title: Optional H1 title. If a non-empty string, the document opens
               with `# {title}`. Otherwise no H1 is emitted.

    Returns:
        A valid Markdown string with exactly one trailing newline, no trailing
        whitespace on any line, and no more than one consecutive blank line.
    """
    if not isinstance(details, dict):
        details = {}

    lines: list[str] = []

    # Optional title
    if title and isinstance(title, str) and title.strip():
        lines.append(f"# {title.strip()}")
        lines.append("")

    # Iterate sections in order
    for i, key in enumerate(DETAILS_SECTION_ORDER):
        # Add section heading
        heading = key.replace("_", " ")
        lines.append(f"## {heading}")
        lines.append("")

        # Handle Summary specially (renders as paragraph)
        if key == DETAILS_SUMMARY_KEY:
            value = details.get(key)
            if isinstance(value, str) and value.strip():
                # Collapse internal whitespace
                text = " ".join(value.split())
                lines.append(text)
            else:
                lines.append("_No summary available._")
        else:
            # Handle list sections
            value = details.get(key)
            items = []

            if isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        # Collapse internal whitespace and skip blanks
                        text = " ".join(item.split())
                        if text:
                            items.append(text)

            if items:
                for item in items:
                    lines.append(f"* {item}")
            else:
                lines.append("_None identified._")

        # Add blank line between sections (but not after the last one)
        if i < len(DETAILS_SECTION_ORDER) - 1:
            lines.append("")

    # Join and clean up
    result = "\n".join(lines)

    # Remove trailing whitespace on each line
    result = "\n".join(line.rstrip() for line in result.split("\n"))

    # Remove multiple consecutive blank lines (keep max 1)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")

    # Ensure exactly one trailing newline
    result = result.rstrip("\n") + "\n"

    return result
