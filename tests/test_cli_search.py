"""CLI read-path tests for live-first search and cache-only mode."""

from __future__ import annotations

import sys

import pytest

from conftest import PANDOC_HAS_JIRA, make_http_error
from cubrid_jira.cli import main
from cubrid_jira.legacy import search_main
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


@pytest.mark.parametrize("command", ["search", "jql", "attachment"])
def test_read_command_help_exposes_server(command, capsys):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])

    assert exc.value.code == 0
    assert "--server" in capsys.readouterr().out


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


def test_search_explicit_server_uses_selected_instance(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "GET",
        "/rest/api/2/issue/RND-2851?expand=renderedFields",
        response=_issue("RND-2851", "alternate server"),
    )

    main([
        "search", "RND-2851",
        "--server", "http://jira.cubrid.com",
        "--no-recurse",
    ])

    out = capsys.readouterr()
    assert "alternate server" in out.out
    assert fake_server.requests[0].url.startswith("http://jira.cubrid.com/")
    assert "jira.cubrid.org" not in fake_server.requests[0].url
    cached = tmp_path / "jira.cubrid.com" / "RND-2851.md"
    assert cached.exists()
    assert "http://jira.cubrid.com/browse/RND-2851" in cached.read_text()


def test_search_infers_server_from_browse_url(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "GET",
        "/rest/api/2/issue/RND-2851?expand=renderedFields",
        response=_issue("RND-2851", "inferred server"),
    )

    main([
        "search", "http://jira.cubrid.com/browse/RND-2851", "--no-recurse",
    ])

    assert "inferred server" in capsys.readouterr().out
    assert fake_server.requests[0].url.startswith("http://jira.cubrid.com/")


def test_search_rejects_conflicting_explicit_and_browse_url_servers(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exc:
        main([
            "search", "http://jira.cubrid.com/browse/RND-2851",
            "--server", "http://jira.cubrid.org",
        ])

    assert exc.value.code == 1
    assert "conflicts with browse URL server" in capsys.readouterr().err
    assert fake_server.requests == []


def test_search_explicit_server_wins_when_browse_url_host_matches(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "GET",
        "/rest/api/2/issue/RND-2851?expand=renderedFields",
        response=_issue("RND-2851", "explicit scheme"),
    )

    main([
        "search", "http://JIRA.CUBRID.COM/browse/RND-2851",
        "--server", "https://jira.cubrid.com",
        "--no-recurse",
    ])

    assert "explicit scheme" in capsys.readouterr().out
    assert fake_server.requests[0].url.startswith("https://jira.cubrid.com/")


def test_search_keeps_recursive_reads_and_related_links_on_selected_server(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    root = _issue("RND-2851", "root")
    root["fields"]["issuelinks"] = [
        {
            "type": {"name": "Relates"},
            "outwardIssue": {"key": "RND-2852"},
        }
    ]
    fake_server.route(
        "GET",
        "/rest/api/2/issue/RND-2851?expand=renderedFields",
        response=root,
    )
    fake_server.route(
        "GET",
        "/rest/api/2/issue/RND-2852?expand=renderedFields",
        response=_issue("RND-2852", "related"),
    )

    main(["search", "RND-2851", "--server", "http://jira.cubrid.com"])

    capsys.readouterr()
    assert len(fake_server.requests) == 2
    assert all(
        request.url.startswith("http://jira.cubrid.com/")
        for request in fake_server.requests
    )
    cache_dir = tmp_path / "jira.cubrid.com"
    assert (cache_dir / "RND-2852.md").exists()
    assert (
        "http://jira.cubrid.com/browse/RND-2852"
        in (cache_dir / "RND-2851.md").read_text()
    )


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


def test_legacy_search_keeps_default_server_compatibility(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("# cached legacy\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["cubrid-jira-search", "CBRD-1", "--cache-only"],
    )

    search_main()

    assert "# cached legacy" in capsys.readouterr().out
    assert fake_server.requests == []
