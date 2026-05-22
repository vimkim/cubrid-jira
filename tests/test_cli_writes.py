"""End-to-end-ish CLI tests for the write subcommands.

These exercise the full argparse → handler → JiraClient → urlopen path with
a fake server, then assert on (a) the recorded HTTP requests and
(b) cache invalidation side effects.
"""

from __future__ import annotations

import json

import pytest

from cubrid_jira.cli import main


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #

def test_create_dry_run_does_not_send(fake_server, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    # No routes registered — any request would assertion-fail.
    main([
        "create",
        "--project", "CBRD",
        "--type", "Bug",
        "--summary", "the title",
        "--priority", "Major",
    ])
    assert fake_server.requests == []
    out = capsys.readouterr()
    body = json.loads(out.out)
    assert body == {
        "fields": {
            "project": {"key": "CBRD"},
            "summary": "the title",
            "issuetype": {"name": "Bug"},
            "priority": {"name": "Major"},
        }
    }


def test_create_live_posts_and_caches(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    # The create POST returns the new key.
    fake_server.route("POST", "/rest/api/2/issue", response={"id": "1", "key": "CBRD-999"})
    # After create we re-fetch via fetcher.fetch_issue (separate code path
    # using urllib too — also via our fake server).
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-999?expand=renderedFields",
        response={"key": "CBRD-999", "fields": {"summary": "hello"}},
    )
    main([
        "create",
        "--project", "CBRD", "--type", "Bug", "--summary", "hello",
        "--yes",
    ])
    # POST then GET.
    methods_paths = [(r.method, r.url) for r in fake_server.requests]
    assert ("POST", "http://jira.cubrid.org/rest/api/2/issue") in methods_paths
    assert any(
        m == "GET" and "/rest/api/2/issue/CBRD-999" in u
        for m, u in methods_paths
    )
    # Cache file was written.
    assert (tmp_path / "CBRD-999.md").exists()


def test_create_with_links_dry_run_uses_placeholder(fake_server, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    main([
        "create",
        "--project", "CBRD", "--type", "Bug", "--summary", "x",
        "--link-relates", "CBRD-1",
        "--link-blocks", "CBRD-2",
    ])
    out = capsys.readouterr()
    # All three bodies print to stdout in dry-run mode.
    assert out.out.count("DRY RUN") == 0  # banner is on stderr
    assert out.err.count("DRY RUN") == 3
    assert "<new-issue-key>" in out.out
    assert '"key": "CBRD-1"' in out.out
    assert '"key": "CBRD-2"' in out.out
    assert '"name": "Relates"' in out.out
    assert '"name": "Blocks"' in out.out


# --------------------------------------------------------------------------- #
# comment
# --------------------------------------------------------------------------- #

def test_comment_invalidates_cache_after_live_post(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-5.md").write_text("stale cache")
    fake_server.route("POST", "/rest/api/2/issue/CBRD-5/comment", response={"id": "1"})

    body_file = tmp_path / "note.md"
    body_file.write_text("a comment body")

    main(["comment", "CBRD-5", "--body-file", str(body_file), "--yes"])

    assert not (tmp_path / "CBRD-5.md").exists()
    rec = fake_server.requests[0]
    assert rec.method == "POST"
    assert rec.url.endswith("/rest/api/2/issue/CBRD-5/comment")
    assert json.loads(rec.body.decode()) == {"body": "a comment body"}


def test_comment_dry_run_keeps_cache(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-5.md").write_text("still here")
    body_file = tmp_path / "note.md"
    body_file.write_text("note")
    main(["comment", "CBRD-5", "--body-file", str(body_file)])
    assert (tmp_path / "CBRD-5.md").exists()
    assert fake_server.requests == []


# --------------------------------------------------------------------------- #
# link
# --------------------------------------------------------------------------- #

def test_link_invalidates_both_sides(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("a")
    (tmp_path / "CBRD-2.md").write_text("b")
    fake_server.route("POST", "/rest/api/2/issueLink", response={})

    main(["link", "CBRD-1", "--type", "Relates", "--to", "CBRD-2", "--yes"])

    assert not (tmp_path / "CBRD-1.md").exists()
    assert not (tmp_path / "CBRD-2.md").exists()
    rec = fake_server.requests[0]
    assert json.loads(rec.body.decode()) == {
        "type": {"name": "Relates"},
        "inwardIssue": {"key": "CBRD-1"},
        "outwardIssue": {"key": "CBRD-2"},
    }


def test_link_rejects_unknown_type(fake_server, capsys):
    with pytest.raises(SystemExit):
        main(["link", "CBRD-1", "--type", "Bogus", "--to", "CBRD-2", "--yes"])
    err = capsys.readouterr().err
    assert "Blocks" in err and "Relates" in err


# --------------------------------------------------------------------------- #
# transition
# --------------------------------------------------------------------------- #

def test_transition_resolves_by_name_then_posts(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-7.md").write_text("stale")
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-7/transitions",
        response={"transitions": [
            {"id": "11", "name": "Open"},
            {"id": "21", "name": "In Progress"},
        ]},
    )
    fake_server.route("POST", "/rest/api/2/issue/CBRD-7/transitions", response={})

    main(["transition", "CBRD-7", "--to", "in progress", "--yes"])

    # First call was the GET, second the POST with the resolved id.
    assert fake_server.requests[0].method == "GET"
    post = fake_server.requests[1]
    assert post.method == "POST"
    assert json.loads(post.body.decode()) == {"transition": {"id": "21"}}
    assert not (tmp_path / "CBRD-7.md").exists()


def test_transition_listing_when_no_to(fake_server, capsys):
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-7/transitions",
        response={"transitions": [{"id": "11", "name": "Open"}]},
    )
    main(["transition", "CBRD-7"])
    out = capsys.readouterr().out
    assert "11: Open" in out


def test_transition_unknown_name_exits(fake_server, capsys):
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-7/transitions",
        response={"transitions": [{"id": "11", "name": "Open"}]},
    )
    with pytest.raises(SystemExit):
        main(["transition", "CBRD-7", "--to", "Frobnicate", "--yes"])
    err = capsys.readouterr().err
    assert "No transition" in err


# --------------------------------------------------------------------------- #
# assign
# --------------------------------------------------------------------------- #

def test_assign_user(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-3.md").write_text("a")
    fake_server.route("PUT", "/rest/api/2/issue/CBRD-3/assignee", response=None)
    main(["assign", "CBRD-3", "--to", "vimkim", "--yes"])
    rec = fake_server.requests[0]
    assert rec.method == "PUT"
    assert json.loads(rec.body.decode()) == {"name": "vimkim"}
    assert not (tmp_path / "CBRD-3.md").exists()


def test_assign_unassign_sends_null(fake_server, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route("PUT", "/rest/api/2/issue/CBRD-3/assignee", response=None)
    main(["assign", "CBRD-3", "--to", "", "--yes"])
    rec = fake_server.requests[0]
    assert json.loads(rec.body.decode()) == {"name": None}
