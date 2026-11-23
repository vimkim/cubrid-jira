"""The terminal summary says when the oracle skipped its Markdown cases.

``pytest -q`` (the repo's addopts) prints no skip summary, and a skipped
oracle is a weaker oracle, so conftest's ``pytest_terminal_summary`` hook
prints one line with the count and both pandoc versions. ``StubReporter``
stands in for pytest's terminal reporter.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import conftest
import render_snapshot as rs


class StubReporter:
    def __init__(self, skipped_reasons: list[str]) -> None:
        # A skipped report's longrepr is (path, line, "Skipped: <reason>").
        self.stats = {
            "skipped": [
                SimpleNamespace(longrepr=("tests/x.py", 1, f"Skipped: {reason}"))
                for reason in skipped_reasons
            ]
        }
        self.lines: list[str] = []

    def write_line(self, line: str, **_markup) -> None:
        self.lines.append(line)


@pytest.fixture
def pandoc(monkeypatch):
    def use(local: str | None, recorded: str | None) -> str | None:
        monkeypatch.setattr(conftest, "PANDOC_VERSION", local)
        monkeypatch.setattr(rs, "load_snapshot", lambda *_: rs.Snapshot(recorded, {}))
        return rs.markdown_skip_reason(local, recorded)

    return use


def test_one_line_names_the_count_and_both_versions(pandoc):
    reason = pandoc("3.1.2", "2.14.0.3")
    reporter = StubReporter([reason] * 3 + ["unrelated skip"])

    conftest.pytest_terminal_summary(reporter)

    assert len(reporter.lines) == 1
    line = reporter.lines[0]
    assert "3 Markdown tests skipped" in line
    assert "2.14.0.3" in line and "3.1.2" in line
    assert "unrelated skip" not in line


def test_a_snapshot_with_no_recorded_pandoc_is_reported_too(pandoc):
    reporter = StubReporter([pandoc("2.14.0.3", None)] * 2)

    conftest.pytest_terminal_summary(reporter)

    assert len(reporter.lines) == 1
    assert "2 Markdown tests skipped" in reporter.lines[0]
    assert "no pandoc version" in reporter.lines[0] and "2.14.0.3" in reporter.lines[0]


def test_nothing_is_printed_when_the_markdown_cases_ran(pandoc):
    pandoc("2.14.0.3", "2.14.0.3")
    reporter = StubReporter(["unrelated skip"])

    conftest.pytest_terminal_summary(reporter)

    assert reporter.lines == []


def test_nothing_is_printed_when_no_markdown_case_was_skipped(pandoc):
    pandoc("3.1.2", "2.14.0.3")
    reporter = StubReporter([])
    conftest.pytest_terminal_summary(reporter)

    assert reporter.lines == []
