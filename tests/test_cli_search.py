"""CLI read-path tests for live-first search and cache-only mode."""

from __future__ import annotations

import sys

import pytest

from conftest import PANDOC_HAS_JIRA, make_http_error
from cubrid_jira.cli import main
from cubrid_jira.walk import bulk_fetch_main


def _issue(key: str, summary: str) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": "Open"},
            "priority": {"name": "Minor"},
            "issuetype": {"name": "Bug"},
            "reporter": {"displayName": "Reporter"},
            "created": "2026-01-01T00:00:00.000+0000",
            "updated": "2026-01-02T00:00:00.000+0000",
        },
    }


def test_search_fetches_live_and_overwrites_stale_cache(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("# stale marker\n", encoding="utf-8")
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-1?expand=renderedFields",
        response=_issue("CBRD-1", "fresh summary"),
    )

    main(["search", "CBRD-1", "--no-recurse"])

    out = capsys.readouterr()
    assert "fresh summary" in out.out
    assert "stale marker" not in out.out
    assert "fresh summary" in (tmp_path / "CBRD-1.md").read_text(encoding="utf-8")
    assert len(fake_server.requests) == 1


@pytest.mark.skipif(not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats")
def test_search_writes_jira_tables_as_round_trip_safe_pipe_tables(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    issue = _issue("CBRD-1", "table body")
    issue["fields"]["description"] = (
        "||ID||내용||분류||\n"
        "|N1|{{has_dealloc_prevent_flag}} :10772 한글|High|\n"
    )
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-1?expand=renderedFields",
        response=issue,
    )

    main(["search", "CBRD-1", "--no-recurse"])

    output = capsys.readouterr().out
    cached = (tmp_path / "CBRD-1.md").read_text(encoding="utf-8")
    for body in (output, cached):
        assert "| ID" in body
        assert "| N1" in body
        assert "has_dealloc_prevent_flag" in body
        assert ":10772" in body
        assert "  ----" not in body


def test_search_force_is_accepted_as_live_fetch_compatibility_flag(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("# stale marker\n", encoding="utf-8")
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-1?expand=renderedFields",
        response=_issue("CBRD-1", "fresh via force"),
    )

    main(["search", "CBRD-1", "--force", "--no-recurse"])

    out = capsys.readouterr()
    assert "fresh via force" in out.out
    assert "stale marker" not in out.out


def test_search_fetch_failure_does_not_print_stale_cache(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("# stale marker\n", encoding="utf-8")
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-1?expand=renderedFields",
        raise_=make_http_error(500, "server down"),
    )

    with pytest.raises(SystemExit) as exc:
        main(["search", "CBRD-1", "--no-recurse"])

    out = capsys.readouterr()
    assert exc.value.code == 1
    assert "stale marker" not in out.out
    assert "Error: Failed to fetch CBRD-1" in out.err
    assert (
        (tmp_path / "CBRD-1.md").read_text(encoding="utf-8")
        == "# stale marker\n"
    )


def test_search_cache_only_uses_cache_without_http(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("# cached only\n", encoding="utf-8")

    main(["search", "CBRD-1", "--cache-only"])

    out = capsys.readouterr()
    assert "# cached only" in out.out
    assert fake_server.requests == []


def test_search_cache_only_miss_fails_without_http(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exc:
        main(["search", "CBRD-1", "--cache-only"])

    out = capsys.readouterr()
    assert exc.value.code == 1
    assert "Error: No cached markdown for CBRD-1" in out.err
    assert fake_server.requests == []


def test_search_cache_only_is_prefix_safe(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-10.md").write_text("# wrong issue\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main(["search", "CBRD-1", "--cache-only"])

    out = capsys.readouterr()
    assert exc.value.code == 1
    assert "wrong issue" not in out.out
    assert "Error: No cached markdown for CBRD-1" in out.err
    assert fake_server.requests == []


def test_legacy_fetch_redownloads_by_default(fake_server, tmp_path, monkeypatch):
    out_dir = tmp_path / "issues"
    out_dir.mkdir()
    (out_dir / "CBRD-1.md").write_text("# stale marker\n", encoding="utf-8")
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-1?expand=renderedFields",
        response=_issue("CBRD-1", "fresh fetch"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["cubrid-jira-fetch", "CBRD-1", "-d", str(out_dir), "--no-recurse"],
    )

    bulk_fetch_main()

    assert "fresh fetch" in (out_dir / "CBRD-1.md").read_text(encoding="utf-8")
    assert len(fake_server.requests) == 1


def test_legacy_fetch_skip_existing_keeps_cache_without_http(
    fake_server, tmp_path, monkeypatch
):
    out_dir = tmp_path / "issues"
    out_dir.mkdir()
    (out_dir / "CBRD-1.md").write_text("# stale marker\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cubrid-jira-fetch",
            "CBRD-1",
            "-d",
            str(out_dir),
            "--no-recurse",
            "--skip-existing",
        ],
    )

    bulk_fetch_main()

    assert (
        (out_dir / "CBRD-1.md").read_text(encoding="utf-8")
        == "# stale marker\n"
    )
    assert fake_server.requests == []
