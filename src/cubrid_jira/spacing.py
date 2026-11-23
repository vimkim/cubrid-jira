"""Spacing for inline markup in Jira wiki bodies.

Jira Server's renderer shows ``{{ }}`` literally when the character just
outside it is a letter or digit in any script, for example ``{{name}}의`` or
``{{name}}s``. Bold and emphasis markers fail the same way next to Hangul only:
``foo_bar_baz`` and ``a*b*c`` are identifiers that Jira also leaves literal,
so those two marker kinds are not spaced from Latin letters or digits. A ``*``
or ``_`` opens or closes a span only at a line edge, next to a character that
is not part of a word, or next to Hangul. ``{{ }}`` spans are found first and
masked, so no marker pairs across a code boundary. Raw Jira write bodies and
Markdown-converted write bodies both pass through this helper before upload.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass

HANGUL = r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]"

CODE_SPAN_RE = re.compile(r"\{\{[^\n{}]*?[^\s{}]\}\}")
EMPHASIS_RE = re.compile(
    r"""
    \*(?!\s)[^*\n]*?[^\s*]\*   |   # *bold*
    _(?!\s)[^_\n]*?[^\s_]_         # _emphasis_
    """,
    re.VERBOSE,
)
# Stands in for a {{code}} span while bold and emphasis are matched, so a
# marker inside code can neither open nor close a span outside it.
CODE_MASK = "\0"
# A {code} or {noformat} macro opens anywhere in a line; only the bare
# {code}/{noformat} of the same name closes it. Jira does not read a tag with a
# brace right next to it as a macro: {{code}} is monospace text.
JIRA_MACRO_OPEN_RE = re.compile(r"(?<!\{)\{(code(?::[^}\n]*)?|noformat)\}(?!\})")
JIRA_MACRO_CLOSE_RE = {
    name: re.compile(rf"(?<!\{{)\{{{name}\}}(?!\}})") for name in ("code", "noformat")
}
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def is_hangul_char(ch: str) -> bool:
    return re.match(HANGUL, ch) is not None


def is_word_char(ch: str) -> bool:
    """True for a letter or a decimal digit in any script.

    This is what makes Jira refuse markup next to a character: ``a``, ``값``,
    ``ª``, ``٣``. A numeral such as ``²``, ``①`` or ``Ⅻ`` (category No or Nl)
    is not one, although ``str.isalnum`` says so. The fixer and the detector
    both ask this function.
    """
    return bool(ch) and (ch.isalpha() or unicodedata.category(ch) == "Nd")


def _is_marker_boundary(ch: str) -> bool:
    """True where a ``*`` or ``_`` may open or close a span.

    Jira needs a line edge or a character that is not part of a word. Hangul
    also counts: Jira refuses ``값*강조*값``, but the author clearly meant bold,
    so the fixer spaces it.
    """
    return not ch or is_hangul_char(ch) or not is_word_char(ch)


def _space_emphasis(masked: str, start: int, end: int, gaps: set[int]) -> None:
    """Record the gaps that Hangul next to a bold or emphasis span needs.

    Only ``masked[start:end]`` is searched, but a span's neighbors are read
    from the whole string, so a nested span sees the marker around it.
    """
    pos = start
    while match := EMPHASIS_RE.search(masked, pos, end):
        first, last = match.span()
        before = masked[first - 1] if first else ""
        after = masked[last] if last < len(masked) else ""
        if not (_is_marker_boundary(before) and _is_marker_boundary(after)):
            # Not a span: its closer may still open the next one.
            pos = first + 1
            continue
        if is_hangul_char(before):
            gaps.add(first)
        if is_hangul_char(after):
            gaps.add(last)
        _space_emphasis(masked, first + 1, last - 1, gaps)
        pos = last


def _fix_inline_spacing(text: str) -> str:
    # ``gaps`` holds the offsets that get a space in front of them.
    gaps: set[int] = set()

    for match in CODE_SPAN_RE.finditer(text):
        first, last = match.span()
        # {{ }} breaks next to any letter or digit.
        if first and is_word_char(text[first - 1]):
            gaps.add(first)
        if last < len(text) and is_word_char(text[last]):
            gaps.add(last)

    masked = CODE_SPAN_RE.sub(lambda m: CODE_MASK * len(m.group()), text)
    _space_emphasis(masked, 0, len(masked), gaps)

    return "".join(" " + ch if index in gaps else ch for index, ch in enumerate(text))


def _prose_spans(text: str) -> Iterator[tuple[int, str, str, list[tuple[int, int]]]]:
    """Yield ``(line_number, content, newline, spans)`` for each line.

    ``spans`` are the ``(start, end)`` offsets in ``content`` that are prose.
    Text inside ``{code}``/``{noformat}`` and fenced blocks is not, and neither
    are the macro tags themselves. A macro may open or close anywhere in a line,
    so one line can hold prose before a macro, the macro's content, and prose
    after it.
    """
    macro: str | None = None
    fence_marker = ""

    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        content = line.rstrip("\r\n")
        newline = line[len(content):]

        fence = FENCE_RE.match(content)
        if fence_marker or (macro is None and fence):
            if not fence_marker:
                fence_marker = fence.group(1)
            elif fence and fence.group(1) == fence_marker:
                fence_marker = ""
            yield number, content, newline, []
            continue

        spans: list[tuple[int, int]] = []
        pos = 0
        while True:
            if macro is None:
                opening = JIRA_MACRO_OPEN_RE.search(content, pos)
                end = opening.start() if opening else len(content)
                if pos < end:
                    spans.append((pos, end))
                if opening is None:
                    break
                macro = opening.group(1).split(":", 1)[0]
                pos = opening.end()
            else:
                closing = JIRA_MACRO_CLOSE_RE[macro].search(content, pos)
                if closing is None:
                    break
                macro = None
                pos = closing.end()
        yield number, content, newline, spans


def normalize_jira_inline_spacing(text: str) -> str:
    """Insert spaces between Jira inline markup and the text it touches.

    ``{{ }}`` gets a space from any adjacent letter or digit; bold and emphasis
    only from adjacent Hangul.

    Code/noformat blocks and Markdown-style fenced blocks are left unchanged,
    including a macro that opens or closes in the middle of a line.
    """
    result: list[str] = []
    for _, content, newline, spans in _prose_spans(text):
        pos = 0
        for start, end in spans:
            result.append(content[pos:start])
            result.append(_fix_inline_spacing(content[start:end]))
            pos = end
        result.append(content[pos:] + newline)
    return "".join(result)


# Deliberately independent of the fixer's span matching: it does not track span
# structure, so it still catches a case the normalizer misses (CBRD-28073
# comment 4778780, where a {{code}} span nested in *bold* kept "}}" against
# Hangul). [^\W_] is a broad net, a letter or digit as str.isalnum() sees it;
# find_markup_contacts keeps the matches whose neighbor is_word_char.
# Jira closes a span at the first "}}" and opens it at the last "{{", so a "}}"
# that follows a "}" and a "{{" that precedes a "{" are literal braces.
CONTACT_RE = re.compile(r"(?<!\})\}\}(?=[^\W_])|(?<=[^\W_])\{\{(?!\{)")


@dataclass(frozen=True)
class MarkupContact:
    """A ``{{``/``}}`` marker touching a letter or digit, which Jira renders literally."""

    line: int
    column: int
    marker: str
    text: str


def find_markup_contacts(text: str) -> list[MarkupContact]:
    """Return every ``}}`` followed by a letter or digit and every ``{{`` after one.

    Lines and columns are 1-based; code/noformat and fenced blocks are skipped.
    """
    contacts: list[MarkupContact] = []
    for number, content, _, spans in _prose_spans(text):
        for start, end in spans:
            for match in CONTACT_RE.finditer(content, start, end):
                marker = match.group(0)
                neighbor = (
                    content[match.end()] if marker == "}}" else content[match.start() - 1]
                )
                if is_word_char(neighbor):
                    contacts.append(
                        MarkupContact(number, match.start() + 1, marker, content)
                    )
    return contacts


class InlineMarkupError(ValueError):
    """A Jira body still has ``{{``/``}}`` touching a letter or digit."""

    def __init__(self, contacts: list[MarkupContact]):
        self.contacts = contacts
        super().__init__(format_contacts(contacts))


def format_contacts(contacts: list[MarkupContact], indent: str = "  ") -> str:
    lines: list[str] = []
    for c in contacts:
        lines.append(f"{indent}line {c.line}, col {c.column}: {c.text}")
    return "\n".join(lines)


def check_inline_markup(text: str) -> None:
    """Raise :class:`InlineMarkupError` if any marker touches a letter or digit."""
    contacts = find_markup_contacts(text)
    if contacts:
        raise InlineMarkupError(contacts)
