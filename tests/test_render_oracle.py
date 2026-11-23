"""Check generated markup against Jira's real renderer.

The expectation for each case comes from its spec (``markup_cases``), never
from the code under test. The renderer's answers are recorded in
``tests/data/jira_render_snapshot.json`` so these tests run offline. Refresh
the snapshot with ``just render-snapshot`` when cases, the normalizer's output,
or the pandoc version change.
"""

from __future__ import annotations

import re
import subprocess

import pytest

import markup_cases as mc
import render_snapshot as rs
from conftest import PANDOC_HAS_JIRA
from cubrid_jira.cli import _convert_jira_body
from cubrid_jira.spacing import find_markup_contacts

requires_pandoc = pytest.mark.skipif(
    not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats"
)


# Oracle failures that a later commit fixes: (test name, param id) -> reason.
# The xfail is strict, so the commit that fixes one must delete its entry.
KNOWN_BROKEN: dict[tuple[str, str], str] = {
    ("converted", "markdown:hostile:double-backtick-code-with-backtick"):
        "finding 1: Markdown-stage spacing puts a space inside the code span",
    ("converted", "jira:hostile:code-macro-on-one-line"):
        "finding 2: one-line {code} content is spaced",
    ("converted", "jira:hostile:noformat-macro-on-one-line"):
        "finding 2: one-line {noformat} content is spaced",
    ("detector", "jira:hostile:code-macro-on-one-line[raw]"):
        "finding 2: detector reads one-line {code} content as prose",
    ("detector", "jira:hostile:noformat-macro-on-one-line[raw]"):
        "finding 2: detector reads one-line {noformat} content as prose",
    ("converted", "jira:hostile:underscore-code-after-snake-prose"):
        "finding 3: a false _ span pairs across the code boundary",
    ("converted", "markdown:hostile:underscore-code-after-snake-prose"):
        "finding 3: a false _ span pairs across the code boundary",
    ("converted", "jira:hostile:star-then-hangul-in-code-after-2*3"):
        "finding 3: a false * span pairs across the code boundary",
    ("converted", "markdown:hostile:star-then-hangul-in-code-after-2*3"):
        "finding 3: a false * span pairs across the code boundary",
    ("detector", "jira:hostile:superscript-after-code[raw]"):
        "finding 6: detector counts a No numeral as a letter or digit",
    ("detector", "jira:hostile:circled-digit-after-code[raw]"):
        "finding 6: detector counts a No numeral as a letter or digit",
    ("detector", "jira:hostile:roman-numeral-after-code[raw]"):
        "finding 6: detector counts an Nl numeral as a letter or digit",
    ("detector", "jira:hostile:brace-after-closing-braces[converted]"):
        "finding 8: detector flags a }} that follows a }",
    ("detector", "jira:hostile:brace-after-closing-braces[raw]"):
        "finding 8: detector flags a }} that follows a }",
    ("detector", "jira:hostile:brace-run-after-letter[converted]"):
        "finding 8: detector flags a {{ that precedes a {",
    ("detector", "jira:hostile:brace-run-after-letter[raw]"):
        "finding 8: detector flags a {{ that precedes a {",
}


def _marks(case: mc.Case, test: str, param_id: str):
    marks = [requires_pandoc] if mc.needs_pandoc(case) else []
    reason = KNOWN_BROKEN.get((test, param_id))
    if reason:
        marks.append(pytest.mark.xfail(strict=True, reason=reason))
    return marks


CASES = [
    pytest.param(case, id=case.id, marks=_marks(case, "converted", case.id))
    for case in mc.all_cases()
]
MARKUPS = [
    pytest.param(
        case, label, id=f"{case.id}[{label}]",
        marks=_marks(case, "detector", f"{case.id}[{label}]"),
    )
    for case in mc.all_cases()
    for label in mc.labels(case)
]


@pytest.fixture(scope="module")
def snapshot() -> dict[str, str]:
    return rs.load_snapshot()


def _html_for(snapshot: dict[str, str], markup: str) -> str:
    if markup not in snapshot:
        pytest.fail(f"no rendered HTML for {markup!r}; run `just render-snapshot`")
    return snapshot[markup]


@pytest.mark.parametrize("case", CASES)
def test_converted_output_renders_as_intended(case, snapshot):
    converted = mc.converted(case)
    html = _html_for(snapshot, converted)
    assert mc.squash_html(html) == mc.squash_html(case.expected_html), (
        f"\n  source:    {case.source!r}"
        f"\n  converted: {converted!r}"
        f"\n  rendered:  {html!r}"
        f"\n  expected:  {case.expected_html!r}"
    )


@pytest.mark.parametrize("case, label", MARKUPS)
def test_detector_agrees_with_renderer(case, label, snapshot):
    """The detector must flag exactly the markup that Jira renders with ``{{``/``}}``."""
    markup = mc.markup(case, label)
    html = _html_for(snapshot, markup)
    flagged = bool(find_markup_contacts(markup))
    literal = mc.renders_literal_braces(html)
    assert flagged == literal, (
        f"\n  markup:   {markup!r}"
        f"\n  detector: {'flags it' if flagged else 'finds nothing'}"
        f"\n  rendered: {html!r}"
    )


def test_comparison_sees_a_space_inside_code_but_not_outside_it():
    """Guards the harness: it must be able to fail on the defects it hunts."""
    squash = mc.squash_html
    assert squash("<p><tt>col_ 이름</tt></p>") != squash("<p><tt>col_이름</tt></p>")
    assert squash('<pre class="code-java">a {{x}} b</pre>') != squash(
        '<pre class="code-java">a{{x}}b</pre>'
    )
    assert squash("<p>a <tt>x</tt> b</p>") == squash("<p>a<tt>x</tt>b</p>")
    assert squash("<p><tt>x&#42;y</tt> 2&#42;3</p>") == squash("<p><tt>x*y</tt> 2*3</p>")


def test_literal_braces_inside_a_code_panel_are_not_a_rendering_failure():
    assert not mc.renders_literal_braces('<pre class="code-java">a{{x}}b</pre>')
    assert mc.renders_literal_braces("<p>a{{x}}b</p>")


@pytest.mark.parametrize("text", mc.IDENTIFIERS)
def test_identifiers_pass_through_unchanged(text):
    assert _convert_jira_body(text, "jira") == text


@requires_pandoc
def test_markdown_specs_mean_what_they_say():
    """Pandoc reads each Markdown source as the spec's chain (guards the harness)."""
    specs = [c for c in mc.all_cases() if c.input_format == "markdown"]
    html = subprocess.run(
        ["pandoc", "--from", "markdown", "--to", "html"],
        input="\n\n".join(c.source for c in specs),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paragraphs = re.findall(r"<p>(.*?)</p>", html, re.DOTALL)
    assert len(paragraphs) == len(specs)
    renames = {"strong": "b", "code": "tt"}
    for spec, paragraph in zip(specs, paragraphs):
        paragraph = re.sub(
            r"<(/?)(strong|code)>", lambda m: f"<{m[1]}{renames[m[2]]}>", paragraph
        )
        assert mc.squash_html(paragraph) == mc.squash_html(spec.expected_html), spec.id


@pytest.mark.live
def test_snapshot_matches_the_live_renderer(snapshot):
    """A sample of the snapshot still renders the same: detects server drift."""
    keys = sorted(snapshot)
    for markup in keys[:: max(1, len(keys) // 40)]:
        assert rs.render_one(markup) == snapshot[markup], markup
