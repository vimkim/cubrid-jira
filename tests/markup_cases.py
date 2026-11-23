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
    """A hand-written Jira source with a hand-written expected rendering."""

    id: str
    source: str
    expected_html: str
    input_format: InputFormat = "jira"


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


def squash_html(html: str) -> str:
    """Drop one outer ``<p>..</p>``, then all whitespace (incl. no-break).

    Spaces the fixer inserts must not matter; markup structure and visible
    text must.
    """
    html = html.strip()
    match = _OUTER_P.match(html)
    if match and "<p>" not in match.group(1):
        html = match.group(1)
    return _WHITESPACE.sub("", html)


def renders_literal_braces(html: str) -> bool:
    """True when Jira left ``{{`` or ``}}`` in the visible output."""
    return "{{" in html or "}}" in html
