"""Generated markup cases for the Jira render oracle.

A case is a *spec*: a chain of inline markers (outer to inner) plus the
characters that touch each marker. The spec is the only source of truth.
It renders three ways without any help from the code under test:

* ``markdown_source``: what an author would type in Markdown,
* ``jira_source``: what an author would type in Jira wiki markup,
* ``expected_html``: what Jira's renderer must produce for the intent.

``converted(case)`` is the one place the code under test runs: it applies the
conversion a write would apply (minus the refuse check). The oracle then
compares Jira's real rendering of that output with ``expected_html``.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Literal

from conftest import PANDOC_HAS_JIRA
from cubrid_jira.cli import _convert_jira_body

InputFormat = Literal["markdown", "jira"]
Kind = Literal["code", "strong", "emph"]
Neighbor = Literal["hangul", "latin", "digit", "punct", "space", "edge"]
Pair = tuple[Neighbor, Neighbor]  # (left, right)

NEIGHBORS: tuple[Neighbor, ...] = (
    "hangul", "latin", "digit", "punct", "space", "edge",
)
_LEFT: dict[Neighbor, str] = {
    "hangul": "앞", "latin": "b", "digit": "7", "punct": ".", "space": " ", "edge": "",
}
_RIGHT: dict[Neighbor, str] = {
    "hangul": "값", "latin": "b", "digit": "7", "punct": ".", "space": " ", "edge": "",
}

# Outermost marker first. Code is always the innermost marker.
CHAINS: tuple[tuple[Kind, ...], ...] = (
    ("code",),
    ("strong",),
    ("emph",),
    ("strong", "code"),
    ("emph", "code"),
    ("emph", "strong"),
    ("strong", "emph"),
    ("emph", "strong", "code"),
)

EDGE: Pair = ("edge", "edge")
SPACE: Pair = ("space", "space")
PAIRS: tuple[Pair, ...] = tuple((l, r) for l in NEIGHBORS for r in NEIGHBORS)


@dataclass(frozen=True)
class Marker:
    markdown: tuple[str, str]
    jira: tuple[str, str]
    html_tag: str


MARKERS: dict[Kind, Marker] = {
    "code": Marker(("`", "`"), ("{{", "}}"), "tt"),
    "strong": Marker(("**", "**"), ("*", "*"), "b"),
    "emph": Marker(("*", "*"), ("_", "_"), "em"),
}


@dataclass(frozen=True)
class Spec:
    """One generated case. Level 0 neighbors sit outside the outermost marker;
    level i > 0 neighbors sit inside marker i-1, around marker i."""

    input_format: InputFormat
    chain: tuple[Kind, ...]
    neighbors: tuple[Pair, ...]

    def __post_init__(self) -> None:
        assert len(self.neighbors) == len(self.chain)
        assert "code" not in self.chain[:-1], "code must be the innermost marker"

    @property
    def id(self) -> str:
        levels = [f"L={l},R={r}" for l, r in self.neighbors]
        tail = "".join(f"|in:{level}" for level in levels[1:])
        return f"{self.input_format}:{'>'.join(self.chain)}:{levels[0]}{tail}"

    @property
    def source(self) -> str:
        return (
            self.markdown_source
            if self.input_format == "markdown"
            else self.jira_source
        )

    @property
    def markdown_source(self) -> str:
        return self._render(self._markdown_delimiters)

    @property
    def jira_source(self) -> str:
        return self._render(lambda level: MARKERS[self.chain[level]].jira)

    @property
    def expected_html(self) -> str:
        def tags(level: int) -> tuple[str, str]:
            tag = MARKERS[self.chain[level]].html_tag
            return f"<{tag}>", f"</{tag}>"

        return self._render(tags)

    def _markdown_delimiters(self, level: int) -> tuple[str, str]:
        kind = self.chain[level]
        if kind != "code" and level > 0 and self.neighbors[level] == EDGE:
            # `***x***` means different things to different Markdown parsers.
            # Underscores keep the order of the two markers unambiguous.
            return ("_", "_") if kind == "emph" else ("__", "__")
        return MARKERS[kind].markdown

    def _render(self, delimiters) -> str:
        def piece(level: int) -> str:
            opener, closer = delimiters(level)
            if level == len(self.chain) - 1:
                inner = "x"
            else:
                left, right = self.neighbors[level + 1]
                inner = _LEFT[left] + piece(level + 1) + _RIGHT[right]
            return opener + inner + closer

        left, right = self.neighbors[0]
        return _LEFT[left] + piece(0) + _RIGHT[right]


@dataclass(frozen=True)
class LiteralCase:
    """A hand-written source with a hand-written expected rendering.

    ``exact`` keeps the whitespace outside code when comparing, for a case
    whose defect would be a stray space in the prose.
    """

    id: str
    source: str
    expected_html: str
    input_format: InputFormat = "jira"
    exact: bool = False


Case = Spec | LiteralCase


def _representable(spec: Spec) -> bool:
    """False when the intent cannot be written down in the source syntax.

    A space directly inside an opening or closing ``*``/``**``/``_`` stops
    that marker from being markup in Markdown and in Jira alike.
    """
    return all(
        "space" not in pair for pair in spec.neighbors[1:]
    )


def _ambiguous(spec: Spec) -> bool:
    """Jira input only: ``foo_bar_baz`` and ``a*b*c`` are identifiers, not markup.

    A bold or emphasis marker with a letter or digit directly outside it has
    no clear intent, at any nesting level. Those combos are not in the "must
    render as markup" set; ``IDENTIFIERS`` pins them to pass through unchanged.
    """
    if spec.input_format != "jira":
        return False
    return any(
        kind != "code" and ({left, right} & {"latin", "digit"})
        for kind, (left, right) in zip(spec.chain, spec.neighbors)
    )


def _specs(input_format: InputFormat) -> list[Spec]:
    seen: dict[tuple, Spec] = {}
    for chain in CHAINS:
        depth = len(chain)
        grids: list[tuple[Pair, ...]] = []
        # Outer neighbors sweep the full grid; inner levels have no neighbors.
        grids += [(pair,) + (EDGE,) * (depth - 1) for pair in PAIRS]
        # Each inner level sweeps the full grid; outer is a space on both sides.
        for level in range(1, depth):
            for pair in PAIRS:
                neighbors = [SPACE] + [EDGE] * (depth - 1)
                neighbors[level] = pair
                grids.append(tuple(neighbors))
        for neighbors in grids:
            spec = Spec(input_format, chain, neighbors)
            if _representable(spec) and not _ambiguous(spec):
                seen.setdefault((chain, neighbors), spec)
    return list(seen.values())


# Real-world lines that Jira rendered literally. Expected HTML is read off the
# render of the same line with the spaces added by hand.
LITERALS: tuple[LiteralCase, ...] = (
    LiteralCase(
        "jira:real:rec-home-in-bold-list-item",
        "* *{{REC_HOME}}이 아닌 serial 행.* 병합은 {{REC_HOME}} 행에서만",
        "<ul><li><b><tt>REC_HOME</tt> 이 아닌 serial 행.</b> 병합은 "
        "<tt>REC_HOME</tt> 행에서만</li></ul>",
    ),
    LiteralCase(
        "jira:real:count-star-in-table-cell",
        "| {{COUNT(*)}}가 START WITH 키 개수를 반환 |",
        # (*) is Jira's star emoticon, in any line. The oracle checks spacing
        # only, so the emoticon stays in the expected HTML.
        "<div class='table-wrap'><table class='confluenceTable'><tbody><tr>"
        "<td class='confluenceTd'><tt>COUNT"
        '<img class="emoticon" '
        'src="https://jira.cubrid.org/images/icons/emoticons/star_yellow.png" '
        'height="16" width="16" align="absmiddle" alt="" border="0"/>'
        "</tt> 가 START WITH 키 개수를 반환</td></tr></tbody></table></div>",
    ),
    LiteralCase(
        "jira:real:min-max-slash",
        "{{MIN}}/{{MAX}}가 WHERE를 무시",
        "<p><tt>MIN</tt>/<tt>MAX</tt> 가 WHERE를 무시</p>",
    ),
    LiteralCase(
        "jira:real:three-code-spans-in-a-sentence",
        "{{OID}}는 {{PAGEID}}와 {{VOLID}}로 구성됨",
        "<p><tt>OID</tt> 는 <tt>PAGEID</tt> 와 <tt>VOLID</tt> 로 구성됨</p>",
    ),
    LiteralCase(
        "jira:real:code-in-bold-no-space-after",
        "*{{name}}이 아닌 행*",
        "<p><b><tt>name</tt> 이 아닌 행</b></p>",
    ),
    LiteralCase(
        "jira:real:already-spaced-control",
        "*{{REC_HOME}} 이 아닌*",
        "<p><b><tt>REC_HOME</tt> 이 아닌</b></p>",
    ),
)

_CODE_PANEL = (
    '<div class="code panel" style="border-width: 1px;">'
    '<div class="codeContent panelContent">\n'
    '<pre class="code-java">{}</pre>\n</div></div>'
)
_NOFORMAT_PANEL = (
    '<div class="preformatted panel" style="border-width: 1px;">'
    '<div class="preformattedContent panelContent">\n<pre>{}</pre>\n</div></div>'
)
_TITLED_NOFORMAT_PANEL = (
    '<div class="preformatted panel" style="border-width: 1px;">'
    '<div class="preformattedHeader panelHeader" style="border-bottom-width: 1px;">'
    "<b>{title}</b></div>"
    '<div class="preformattedContent panelContent">\n<pre>{text}</pre>\n</div></div>'
)
# What Jira renders for a {code} or {noformat} tag that has no closing tag.
_EMPTY_CODE = _CODE_PANEL.format("")
_EMPTY_NOFORMAT = _NOFORMAT_PANEL.format("")

# Code text that is hostile to the inline fixers: underscores, stars, backticks
# and braces inside code, prose with identifiers before it, and macros that
# share a line with content. The oracle compares text inside code exactly, so a
# space inserted into the code fails. Raw Jira braces at the inner edge of
# code are not here: that is a known gap (see the follow-up issues).
HOSTILE: tuple[LiteralCase, ...] = (
    LiteralCase(
        "jira:hostile:underscore-code-after-snake-prose",
        "my_table의 {{col_이름}} 컬럼",
        "<p>my_table의 <tt>col_이름</tt> 컬럼</p>",
    ),
    LiteralCase(
        "markdown:hostile:underscore-code-after-snake-prose",
        "my_table의 `col_이름` 컬럼",
        "<p>my_table의 <tt>col_이름</tt> 컬럼</p>",
        "markdown",
    ),
    LiteralCase(
        "jira:hostile:star-code-after-2*3",
        "2*3 {{a*b}} 끝",
        "<p>2*3 <tt>a*b</tt> 끝</p>",
    ),
    LiteralCase(
        "jira:hostile:star-then-hangul-in-code-after-2*3",
        "2*3 {{x*값}} 끝",
        "<p>2*3 <tt>x*값</tt> 끝</p>",
    ),
    LiteralCase(
        "markdown:hostile:star-code-after-2*3",
        "2*3 `a*b` 끝",
        "<p>2*3 <tt>a*b</tt> 끝</p>",
        "markdown",
    ),
    LiteralCase(
        "markdown:hostile:star-then-hangul-in-code-after-2*3",
        "2*3 `x*값` 끝",
        "<p>2*3 <tt>x*값</tt> 끝</p>",
        "markdown",
    ),
    LiteralCase(
        "markdown:hostile:double-backtick-code-with-backtick",
        "``a`b``값",
        "<p><tt>a`b</tt> 값</p>",
        "markdown",
    ),
    LiteralCase(
        "markdown:hostile:escaped-backticks",
        "quote with \\`a\\`b here",
        "<p>quote with `a`b here</p>",
        "markdown",
        # Round 1 wrote "`a` b": the space is in the prose, not in code.
        exact=True,
    ),
    LiteralCase(
        "markdown:hostile:braces-inside-code",
        "앞 `m{k}x` 뒤",
        "<p>앞 <tt>m{k}x</tt> 뒤</p>",
        "markdown",
    ),
    LiteralCase(
        "jira:hostile:escaped-braces-inside-code",
        "앞 {{m\\{k\\}x}} 뒤",
        "<p>앞 <tt>m{k}x</tt> 뒤</p>",
    ),
    LiteralCase(
        "jira:hostile:code-macro-on-one-line",
        "{code}print(a{{x}}b){code}",
        _CODE_PANEL.format("print(a{{x}}b)"),
    ),
    LiteralCase(
        "jira:hostile:noformat-macro-on-one-line",
        "{noformat}a{{b}}c{noformat}",
        _NOFORMAT_PANEL.format("a{{b}}c"),
    ),
    LiteralCase(
        "jira:hostile:prose-around-code-macro",
        "앞{{a}}값 {code}z{code} 뒤{{b}}값",
        "<p>앞<tt>a</tt>값</p>" + _CODE_PANEL.format("z") + "<p>뒤<tt>b</tt>값</p>",
    ),
    # Literal stars and underscores between Hangul. Pandoc writes them bare
    # (it drops the backslash), and Jira leaves a bare pair next to Hangul
    # literal, so the Markdown path must not read them as bold or emphasis.
    LiteralCase(
        "markdown:hostile:escaped-stars-between-hangul",
        "값\\*굵게\\*값",
        "<p>값*굵게*값</p>",
        "markdown",
    ),
    LiteralCase(
        "markdown:hostile:escaped-underscores-between-hangul",
        "사용자\\_이름\\_코드",
        "<p>사용자_이름_코드</p>",
        "markdown",
    ),
    LiteralCase(
        "markdown:hostile:snake-case-between-hangul",
        "사용자_이름_코드",
        "<p>사용자_이름_코드</p>",
        "markdown",
    ),
    LiteralCase(
        "jira:hostile:superscript-after-code",
        "{{x}}²",
        "<p><tt>x</tt>²</p>",
    ),
    LiteralCase(
        "jira:hostile:circled-digit-after-code",
        "{{x}}①",
        "<p><tt>x</tt>①</p>",
    ),
    LiteralCase(
        "jira:hostile:roman-numeral-after-code",
        "{{x}}Ⅻ",
        "<p><tt>x</tt>Ⅻ</p>",
    ),
    LiteralCase(
        "jira:hostile:arabic-digit-after-code",
        "{{x}}٣",
        "<p><tt>x</tt>٣</p>",
    ),
    LiteralCase(
        "jira:hostile:ordinal-letter-after-code",
        "{{x}}ª",
        "<p><tt>x</tt>ª</p>",
    ),
    LiteralCase(
        "jira:hostile:brace-after-closing-braces",
        "{{x}}}값",
        "<p><tt>x</tt>}값</p>",
    ),
    LiteralCase(
        "jira:hostile:brace-run-after-letter",
        "a{{{x}} b",
        "<p>a{<tt>x</tt> b</p>",
    ),
    # {code}/{noformat} tags that are not a block. Jira pairs tags in order over
    # the whole body, reads \{code} as text, and renders a tag with no closing
    # tag as an empty panel, then keeps reading prose. Expected HTML is the
    # control render of the same body with the space added by hand.
    LiteralCase(
        "jira:hostile:escaped-code-tag-before-code",
        "Wrap it in \\{code} please.\n{{x}}값",
        "<p>Wrap it in {code} please.<br/>\n<tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:mentioned-noformat-tag",
        "Use the {noformat} macro for logs.\n\n{{x}}값 here",
        "<p>Use the </p>" + _EMPTY_NOFORMAT
        + "<p> macro for logs.</p><p><tt>x</tt> 값 here</p>",
    ),
    LiteralCase(
        "jira:hostile:two-mentioned-tags",
        "The macros {code} and {noformat} are fine.\n{{x}}값",
        "<p>The macros </p>" + _EMPTY_CODE + "<p> and </p>" + _EMPTY_NOFORMAT
        + "<p> are fine.<br/>\n<tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:code-tag-inside-a-word",
        "a{code}b {{x}}값",
        "<p>a</p>" + _EMPTY_CODE + "<p>b <tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:unclosed-code-tag-with-language",
        "x {code:java} y {{z}}값",
        "<p>x </p>" + _EMPTY_CODE + "<p> y <tt>z</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:odd-number-of-code-tags",
        "{code}a{code}b{code} {{x}}값",
        _CODE_PANEL.format("a") + "<p>b</p>" + _EMPTY_CODE
        + "<p> <tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:escaped-noformat-tag",
        "text \\{noformat} {{x}}값",
        "<p>text {noformat} <tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:code-tag-alone-on-its-line-never-closed",
        "{code}\n{{x}}값",
        _EMPTY_CODE + "<p><tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:escaped-closing-tag-inside-code",
        "{code}a\\{code}b{code} {{x}}값",
        _CODE_PANEL.format("a\\{code}b") + "<p> <tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:two-backslashes-are-a-line-break-not-an-escape",
        "\\\\{code}x{code} {{y}}값",
        '<p><br class="atl-forced-newline" /></p>' + _CODE_PANEL.format("x")
        + "<p> <tt>y</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:three-backslashes-escape-the-tag",
        "\\\\\\{code}x{code} {{y}}값",
        "<p>\\\\\\{code}x</p>" + _EMPTY_CODE + "<p> <tt>y</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:noformat-tag-inside-code",
        "{code}a{noformat}b{code} {{x}}값",
        _CODE_PANEL.format("a{noformat}b") + "<p> <tt>x</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:noformat-with-title",
        "{noformat:title=T}a{{b}}c{noformat} {{y}}값",
        _TITLED_NOFORMAT_PANEL.format(title="T", text="a{{b}}c")
        + "<p> <tt>y</tt> 값</p>",
    ),
    LiteralCase(
        "jira:hostile:unclosed-code-tag-then-more-paragraphs",
        "text\n{code}\nunclosed {{x}}값\n\nmore {{y}}값",
        "<p>text</p>" + _EMPTY_CODE
        + "<p>unclosed <tt>x</tt> 값</p><p>more <tt>y</tt> 값</p>",
    ),
)

# Identifier-like text. Never markup, so a write must not touch it.
IDENTIFIERS: tuple[str, ...] = (
    "foo_bar_baz",
    "a*b*c",
    "snake_case_name",
    "CBRD_28073_fix",
    "x_1_y",
    "2*3*4",
    "a*b + c*d",
    "see foo_bar_baz.",
)


@functools.cache
def all_cases() -> tuple[Case, ...]:
    return (
        *_specs("jira"),
        *_specs("markdown"),
        *LITERALS,
        *HOSTILE,
    )


@functools.cache
def converted(case: Case) -> str:
    """What a write applies to this case's source (no refuse check)."""
    return _convert_jira_body(case.source, case.input_format)


def needs_pandoc(case: Case) -> bool:
    return case.input_format == "markdown"


def labels(case: Case) -> tuple[str, ...]:
    """The markup strings the oracle checks for one case.

    ``raw`` is the unconverted Jira source, so the detector can be tried on
    text that no fixer has touched. Markdown has no Jira-side raw form.
    """
    return ("converted", "raw") if case.input_format == "jira" else ("converted",)


def markup(case: Case, label: str) -> str:
    return converted(case) if label == "converted" else case.source


def needed_markups(*, pandoc: bool = PANDOC_HAS_JIRA) -> list[str]:
    """Every distinct markup string the snapshot must hold, sorted."""
    return sorted(
        {
            markup(case, label)
            for case in all_cases()
            if pandoc or not needs_pandoc(case)
            for label in labels(case)
        }
    )


# ---- comparison ------------------------------------------------------------ #

_OUTER_P = re.compile(r"\A<p>(.*)</p>\Z", re.DOTALL)
_WHITESPACE = re.compile(r"\s+|&nbsp;")
_NUMERIC_REF = re.compile(r"&#(\d+);")
# Text Jira shows as code: <tt> for {{ }}, <pre> for {code} and {noformat}.
_CODE_TEXT = re.compile(r"(<tt>.*?</tt>|<pre\b[^>]*>.*?</pre>)", re.DOTALL)
_PRE = re.compile(r"<pre\b[^>]*>.*?</pre>", re.DOTALL)


def exact_html(html: str) -> str:
    """Rendered HTML with one outer ``<p>..</p>`` dropped, whitespace kept.

    Jira writes an escaped ``*`` as ``&#42;``; numeric references are decoded so
    both spellings compare equal.
    """
    html = _NUMERIC_REF.sub(lambda m: chr(int(m.group(1))), html.strip())
    match = _OUTER_P.match(html)
    if match and "<p>" not in match.group(1):
        html = match.group(1)
    return html


def squash_html(html: str) -> str:
    """Normalize rendered HTML for comparison.

    Drops whitespace (incl. no-break) *outside* code from :func:`exact_html`,
    so spaces the fixer inserts next to a span do not matter. Text inside
    ``<tt>`` and ``<pre>`` is compared exactly: a space inserted into code
    changes what the reader sees.
    """
    parts = _CODE_TEXT.split(exact_html(html))
    return "".join(
        part if index % 2 else _WHITESPACE.sub("", part)
        for index, part in enumerate(parts)
    )


def comparable(case: Case, html: str) -> str:
    """``html`` normalized the way ``case`` is compared: exactly or squashed."""
    exact = isinstance(case, LiteralCase) and case.exact
    return exact_html(html) if exact else squash_html(html)


def renders_literal_braces(html: str) -> bool:
    """True when Jira left ``{{`` or ``}}`` in the visible markup.

    ``{code}``/``{noformat}`` panels show their text verbatim, braces
    included, so ``<pre>`` content does not count.
    """
    prose = _PRE.sub("", html)
    return "{{" in prose or "}}" in prose
