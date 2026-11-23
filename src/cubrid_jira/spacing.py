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
# A {code} or {noformat} tag, with or without parameters ({code:java},
# {noformat:title=T}). Jira does not read a tag with a brace right next to it
# as a macro: {{code}} is monospace text. Only the bare tag of the same name
# closes a macro.
JIRA_MACRO_OPEN_RE = re.compile(r"(?<!\{)\{(code|noformat)(?::[^}\n]*)?\}(?!\})")
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


def _is_escaped(text: str, index: int) -> bool:
    """True when an odd run of backslashes ends just before ``text[index]``.

    Jira shows ``\\{code}`` as text. Two backslashes are a line break, so a tag
    after them is live; three escape it again.
    """
    run = 0
    while index > run and text[index - run - 1] == "\\":
        run += 1
    return run % 2 == 1


def _find_tag(pattern: re.Pattern[str], text: str, pos: int) -> re.Match[str] | None:
    """The first match of ``pattern`` at or after ``pos`` that is not escaped."""
    return next(
        (m for m in pattern.finditer(text, pos) if not _is_escaped(text, m.start())),
        None,
    )


def _verbatim_ranges(
    text: str, fences: list[tuple[int, int, str]]
) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` offsets of the text that is not prose, in order.

    Jira pairs ``{code}``/``{noformat}`` tags in order over the whole body: a
    tag opens a block that a later bare tag of the same name closes, and the
    tags themselves are not prose. A tag with no closing tag opens nothing:
    Jira renders an empty panel and reads on, so only the tag is skipped.
    ``fences`` holds ``(line_start, line_end, marker)`` for each line that
    starts with a Markdown fence marker. Whichever of a fence and a tag comes
    first claims the text after it, so a fence marker inside a macro is code.
    """
    ranges: list[tuple[int, int]] = []
    pos = 0
    index = 0
    while True:
        opening = _find_tag(JIRA_MACRO_OPEN_RE, text, pos)
        while index < len(fences) and fences[index][0] < pos:
            index += 1
        fence = fences[index] if index < len(fences) else None
        if fence and (opening is None or fence[0] <= opening.start()):
            closing = next((f for f in fences[index + 1:] if f[2] == fence[2]), None)
            start, end = fence[0], closing[1] if closing else len(text)
        elif opening:
            closing = _find_tag(
                JIRA_MACRO_CLOSE_RE[opening.group(1)], text, opening.end()
            )
            start, end = opening.start(), (closing or opening).end()
        else:
            return ranges
        ranges.append((start, end))
        pos = end


def _prose_spans(text: str) -> Iterator[tuple[int, str, str, list[tuple[int, int]]]]:
    """Yield ``(line_number, content, newline, spans)`` for each line.

    ``spans`` are the ``(start, end)`` offsets in ``content`` that are prose.
    Text inside ``{code}``/``{noformat}`` and fenced blocks is not, and neither
    are the macro tags themselves. A macro may open or close anywhere in a line,
    so one line can hold prose before a macro, the macro's content, and prose
    after it. See :func:`_verbatim_ranges` for how tags pair.
    """
    lines: list[tuple[int, str, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        lines.append((offset, content, line[len(content):]))
        offset += len(line)

    fences = [
        (start, start + len(content), match.group(1))
        for start, content, _ in lines
        if (match := FENCE_RE.match(content))
    ]
    verbatim = _verbatim_ranges(text, fences)

    index = 0
    for number, (start, content, newline) in enumerate(lines, start=1):
        end = start + len(content)
        spans: list[tuple[int, int]] = []
        cursor = start
        while index < len(verbatim) and verbatim[index][0] < end:
            first, last = verbatim[index]
            if first > cursor:
                spans.append((cursor - start, first - start))
            cursor = max(cursor, last)
            if last > end:  # the range goes on into the next line
                break
            index += 1
        if cursor < end:
            spans.append((cursor - start, end - start))
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
