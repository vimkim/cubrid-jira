"""The oracle snapshot records which pandoc wrote its Markdown keys.

Offline: nothing here calls Jira. ``fake_render`` stands in for the renderer.
"""

from __future__ import annotations

import json

import render_snapshot as rs

OLD = rs.Snapshot("2.14.0.3", {"jira": "<p>j</p>", "md-old": "<p>old</p>"})


def fake_render(markups: list[str]) -> list[str]:
    return [f"<p>{m}</p>" for m in markups]


def needed(markdown: bool) -> list[str]:
    return ["jira", "jira-new", *(["md-new"] if markdown else [])]


def test_the_file_holds_the_pandoc_version_next_to_the_renders(tmp_path):
    path = tmp_path / "snapshot.json"

    rs.write_snapshot(OLD, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "pandoc": "2.14.0.3",
        "renders": {"jira": "<p>j</p>", "md-old": "<p>old</p>"},
    }
    assert rs.load_snapshot(path) == OLD


def test_a_missing_snapshot_is_empty_and_has_no_pandoc_version(tmp_path):
    assert rs.load_snapshot(tmp_path / "none.json") == rs.Snapshot(None, {})


def test_markdown_cases_are_skipped_when_the_pandoc_version_differs():
    reason = rs.markdown_skip_reason("3.1.2", "2.14.0.3")

    assert reason is not None
    assert "2.14.0.3" in reason and "3.1.2" in reason


def test_markdown_cases_run_when_the_version_matches():
    assert rs.markdown_skip_reason("2.14.0.3", "2.14.0.3") is None


def test_markdown_cases_are_skipped_when_the_snapshot_records_no_pandoc():
    """A ``"pandoc": null`` snapshot says nothing about which pandoc wrote its keys."""
    reason = rs.markdown_skip_reason("2.14.0.3", None)

    assert reason is not None
    assert "no pandoc version" in reason and "2.14.0.3" in reason


def test_markdown_cases_are_skipped_without_a_pandoc_that_reads_jira():
    assert rs.markdown_skip_reason(None, "2.14.0.3") is not None


def test_same_version_renders_the_missing_and_prunes_the_unused():
    new, rendered, skipped = rs.refresh_snapshot(
        OLD, "2.14.0.3", needed, render=fake_render
    )

    assert skipped is None
    assert rendered == 2
    assert new == rs.Snapshot(
        "2.14.0.3",
        {"jira": "<p>j</p>", "jira-new": "<p>jira-new</p>", "md-new": "<p>md-new</p>"},
    )


def test_a_skipped_markdown_run_prunes_nothing_and_keeps_the_recorded_version():
    new, rendered, skipped = rs.refresh_snapshot(
        OLD, "3.1.2", needed, render=fake_render
    )

    assert rendered == 1
    assert "2.14.0.3" in skipped and "3.1.2" in skipped
    assert new == rs.Snapshot(
        "2.14.0.3",
        {"jira": "<p>j</p>", "jira-new": "<p>jira-new</p>", "md-old": "<p>old</p>"},
    )


def test_no_pandoc_prunes_nothing_either():
    new, _, skipped = rs.refresh_snapshot(OLD, None, needed, render=fake_render)

    assert skipped is not None
    assert "md-old" in new.renders and new.pandoc == "2.14.0.3"


def test_adopt_pandoc_records_the_local_version_and_prunes_the_old_markdown():
    new, rendered, skipped = rs.refresh_snapshot(
        OLD, "3.1.2", needed, adopt=True, render=fake_render
    )

    assert skipped is None
    assert rendered == 2
    assert new == rs.Snapshot(
        "3.1.2",
        {"jira": "<p>j</p>", "jira-new": "<p>jira-new</p>", "md-new": "<p>md-new</p>"},
    )


def test_adopt_pandoc_cannot_record_a_pandoc_that_is_missing():
    new, _, skipped = rs.refresh_snapshot(
        OLD, None, needed, adopt=True, render=fake_render
    )

    assert skipped is not None
    assert new.pandoc == "2.14.0.3" and "md-old" in new.renders


def test_a_null_snapshot_skips_markdown_on_refresh_until_a_pandoc_is_adopted():
    null = rs.Snapshot(None, {"jira": "<p>j</p>"})

    new, rendered, skipped = rs.refresh_snapshot(
        null, "2.14.0.3", needed, render=fake_render
    )
    assert "no pandoc version" in skipped
    assert rendered == 1 and new.pandoc is None and "md-new" not in new.renders

    adopted, rendered, skipped = rs.refresh_snapshot(
        null, "2.14.0.3", needed, adopt=True, render=fake_render
    )
    assert skipped is None and rendered == 2
    assert adopted.pandoc == "2.14.0.3" and "md-new" in adopted.renders
