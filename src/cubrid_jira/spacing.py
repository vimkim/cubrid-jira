"""Korean-aware spacing for Jira wiki bodies.

Jira Server can fail to recognize inline wiki markup when the marker touches
Korean text directly, for example ``{{name}}의``.  The write commands still
send raw Jira wiki text; this module only inserts boundary spaces around known
inline markup spans.
"""

from __future__ import annotations

import re

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


def normalize_korean_jira_spacing(text: str) -> str:
    """Insert spaces between Korean text and Jira inline markup.

    Code/noformat blocks and Markdown-style fenced blocks are left unchanged.
    """
    result: list[str] = []
    jira_block: str | None = None
    fence_marker = ""

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content):]

        jira_match = JIRA_BLOCK_RE.match(content)
        if jira_match:
            block_name = jira_match.group(1).split(":", 1)[0]
            if jira_block is None:
                jira_block = block_name
            elif jira_block == block_name:
                jira_block = None
            result.append(line)
            continue

        fence_match = FENCE_RE.match(content)
        if fence_match:
            marker = fence_match.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = ""
            result.append(line)
            continue

        if jira_block is not None or fence_marker:
            result.append(line)
        else:
            result.append(_fix_inline_spacing(content) + newline)

    return "".join(result)
