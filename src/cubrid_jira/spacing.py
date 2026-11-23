"""Korean-aware spacing for Jira wiki bodies.

Jira Server can fail to recognize inline wiki markup when the marker touches
Korean text directly, for example ``{{name}}의``. Raw Jira write bodies and
Markdown-converted write bodies both pass through this helper before upload.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

KOREAN = r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]"

INLINE_JIRA_RE = re.compile(
    r"""
    \{\{[^\n{}]*?[^\s{}]\}\}     |   # {{monospace}}
    \*(?!\s)([^*\n]*?[^\s*])\*   |   # *bold*
    _(?!\s)([^_\n]*?[^\s_])_         # _emphasis_
    """,
    re.VERBOSE,
)
JIRA_BLOCK_RE = re.compile(r"^\s*\{(code(?::[^}]*)?|noformat)\}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _is_korean_char(ch: str) -> bool:
    return re.match(KOREAN, ch) is not None


def _fix_inline_spacing(text: str) -> str:
    result: list[str] = []
    last = 0

    for match in INLINE_JIRA_RE.finditer(text):
        start, end = match.span()
        span = match.group(0)
        if not span.startswith("{{"):
            # Bold/emphasis: also space the code spans nested inside it.
            span = span[0] + _fix_inline_spacing(span[1:-1]) + span[-1]

        result.append(text[last:start])

        prev_char = text[start - 1] if start > 0 else ""
        next_char = text[end] if end < len(text) else ""

        if prev_char and _is_korean_char(prev_char):
            if not result[-1].endswith(" "):
                result.append(" ")

        result.append(span)

        if next_char and _is_korean_char(next_char):
            result.append(" ")

        last = end

    result.append(text[last:])
    return "".join(result)


def _lines_outside_blocks(text: str) -> Iterator[tuple[int, str, str, bool]]:
    """Yield ``(line_number, content, newline, is_prose)`` for each line.

    ``is_prose`` is False inside ``{code}``/``{noformat}`` and fenced blocks,
    and on the lines that open or close them.
    """
    jira_block: str | None = None
    fence_marker = ""

    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        content = line.rstrip("\r\n")
        newline = line[len(content):]

        jira_match = JIRA_BLOCK_RE.match(content)
        if jira_match:
            block_name = jira_match.group(1).split(":", 1)[0]
            if jira_block is None:
                jira_block = block_name
            elif jira_block == block_name:
                jira_block = None
            yield number, content, newline, False
            continue

        fence_match = FENCE_RE.match(content)
        if fence_match:
            marker = fence_match.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = ""
            yield number, content, newline, False
            continue

        yield number, content, newline, jira_block is None and not fence_marker


def normalize_korean_jira_spacing(text: str) -> str:
    """Insert spaces between Korean text and Jira inline markup.

    Code/noformat blocks and Markdown-style fenced blocks are left unchanged.
    """
    result: list[str] = []
    for _, content, newline, is_prose in _lines_outside_blocks(text):
        if is_prose:
            result.append(_fix_inline_spacing(content) + newline)
        else:
            result.append(content + newline)
    return "".join(result)


# Deliberately independent of INLINE_JIRA_RE: it does not track span structure,
# so it still catches a case the normalizer misses (CBRD-28073 comment 4778780,
# where a {{code}} span nested in *bold* kept "}}" against Hangul).
CONTACT_RE = re.compile(rf"\}}\}}(?={KOREAN})|(?<={KOREAN})\{{\{{")


@dataclass(frozen=True)
class MarkupContact:
    """A ``{{``/``}}`` marker touching Hangul, which Jira renders literally."""

    line: int
    column: int
    marker: str
    text: str


def find_korean_markup_contacts(text: str) -> list[MarkupContact]:
    """Return every ``}}`` followed by Hangul and every ``{{`` after Hangul.

    Lines and columns are 1-based; code/noformat and fenced blocks are skipped.
    """
    contacts: list[MarkupContact] = []
    for number, content, _, is_prose in _lines_outside_blocks(text):
        if not is_prose:
            continue
        for match in CONTACT_RE.finditer(content):
            contacts.append(
                MarkupContact(number, match.start() + 1, match.group(0), content)
            )
    return contacts


class KoreanMarkupError(ValueError):
    """A Jira body still has ``{{``/``}}`` touching Hangul."""

    def __init__(self, contacts: list[MarkupContact]):
        self.contacts = contacts
        super().__init__(format_contacts(contacts))


def format_contacts(contacts: list[MarkupContact], indent: str = "  ") -> str:
    lines: list[str] = []
    for c in contacts:
        lines.append(f"{indent}line {c.line}, col {c.column}: {c.text}")
    return "\n".join(lines)


def check_korean_markup(text: str) -> None:
    """Raise :class:`KoreanMarkupError` if any marker touches Hangul."""
    contacts = find_korean_markup_contacts(text)
    if contacts:
        raise KoreanMarkupError(contacts)
