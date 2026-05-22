"""--output json contract for every write subcommand.

In live mode each command emits ONE JSON line on stdout with a stable shape.
In dry-run mode it emits ``{"dry_run": true, "requests": [...]}`` capturing
every request the live run would have sent — including the create+links
3-request plan, which is the agent-friendly headline feature.
"""

from __future__ import annotations

import json

from cubrid_jira.cli import main


def _sole_stdout_json(capsys) -> dict:
    out = capsys.readouterr().out.strip()
    assert out, "expected exactly one JSON line on stdout"
    assert "\n" not in out, f"expected ONE line, got:\n{out!r}"
    return json.loads(out)


# --------------------------------------------------------------------------- #
# Dry-run JSON plans
# --------------------------------------------------------------------------- #

def test_create_dry_run_json_captures_plan(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    main([
        "create",
        "--project", "CBRD", "--type", "Bug", "--summary", "x",
        "--link-relates", "CBRD-1",
        "--link-blocks", "CBRD-2",
        "--output", "json",
    ])
    plan = _sole_stdout_json(capsys)
    assert plan["dry_run"] is True
    assert len(plan["requests"]) == 3  # create + 2 links
    methods = [r["method"] for r in plan["requests"]]
    assert methods == ["POST", "POST", "POST"]
    # Issue create URL.
    assert plan["requests"][0]["url"].endswith("/rest/api/2/issue")
    # Both link POSTs point at the issueLink endpoint with the placeholder.
    for link_req in plan["requests"][1:]:
        assert link_req["url"].endswith("/rest/api/2/issueLink")
        assert link_req["body"]["inwardIssue"]["key"] == "<new-issue-key>"
    assert fake_server.requests == []  # nothing was actually sent


def test_link_dry_run_json(fake_server, capsys):
    main([
        "link", "CBRD-1", "--type", "Relates", "--to", "CBRD-2",
        "--output", "json",
    ])
    plan = _sole_stdout_json(capsys)
    assert plan == {
        "dry_run": True,
        "requests": [{
            "method": "POST",
            "url": "http://jira.cubrid.org/rest/api/2/issueLink",
            "body": {
                "type": {"name": "Relates"},
                "inwardIssue": {"key": "CBRD-1"},
                "outwardIssue": {"key": "CBRD-2"},
            },
        }],
    }


# --------------------------------------------------------------------------- #
# Live JSON success shapes
# --------------------------------------------------------------------------- #

def test_create_live_json_shape(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "POST", "/rest/api/2/issue",
        response={
            "id": "12345",
            "key": "CBRD-999",
            "self": "http://jira.cubrid.org/rest/api/2/issue/12345",
        },
    )
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-999?expand=renderedFields",
        response={"key": "CBRD-999", "fields": {"summary": "hi"}},
    )
    main([
        "create",
        "--project", "CBRD", "--type", "Bug", "--summary", "hi",
        "--yes", "--output", "json",
    ])
    result = _sole_stdout_json(capsys)
    # Agents chain on `key` to do anything useful (link, comment, transition);
    # `id` and `self` round out the canonical Jira create response so chained
    # callers don't need a second GET to reconstruct it.
    assert result == {
        "key": "CBRD-999",
        "id": "12345",
        "self": "http://jira.cubrid.org/rest/api/2/issue/12345",
        "url": "http://jira.cubrid.org/browse/CBRD-999",
    }


def test_comment_live_json_shape(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "POST", "/rest/api/2/issue/CBRD-5/comment",
        response={"id": "42", "body": "..."},
    )
    body_file = tmp_path / "note.md"
    body_file.write_text("hi")
    main([
        "comment", "CBRD-5", "--body-file", str(body_file),
        "--yes", "--output", "json",
    ])
    result = _sole_stdout_json(capsys)
    assert result == {"issue": "CBRD-5", "comment_id": "42"}


def test_link_live_json_shape(fake_server, capsys):
    fake_server.route("POST", "/rest/api/2/issueLink", response={})
    main([
        "link", "CBRD-1", "--type", "Relates", "--to", "CBRD-2",
        "--yes", "--output", "json",
    ])
    assert _sole_stdout_json(capsys) == {
        "inward": "CBRD-1", "outward": "CBRD-2", "type": "Relates",
    }


def test_transition_live_json_shape(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-7/transitions",
        response={"transitions": [{"id": "21", "name": "In Progress"}]},
    )
    fake_server.route("POST", "/rest/api/2/issue/CBRD-7/transitions", response={})
    main([
        "transition", "CBRD-7", "--to", "in progress",
        "--yes", "--output", "json",
    ])
    assert _sole_stdout_json(capsys) == {
        "issue": "CBRD-7", "transition_id": "21", "to": "in progress",
    }


def test_assign_live_json_shape(fake_server, capsys):
    fake_server.route("PUT", "/rest/api/2/issue/CBRD-3/assignee", response=None)
    main(["assign", "CBRD-3", "--to", "vimkim", "--yes", "--output", "json"])
    assert _sole_stdout_json(capsys) == {"issue": "CBRD-3", "assignee": "vimkim"}


def test_assign_unassign_json_shape(fake_server, capsys):
    fake_server.route("PUT", "/rest/api/2/issue/CBRD-3/assignee", response=None)
    main(["assign", "CBRD-3", "--to", "", "--yes", "--output", "json"])
    assert _sole_stdout_json(capsys) == {"issue": "CBRD-3", "assignee": None}


def test_update_live_json_shape(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route("PUT", "/rest/api/2/issue/CBRD-9", response=None)
    body_file = tmp_path / "body.md"
    body_file.write_text("hello")
    main([
        "update", "CBRD-9",
        "--summary", "t",
        "--description-file", str(body_file),
        "--yes", "--output", "json",
    ])
    assert _sole_stdout_json(capsys) == {
        "issue": "CBRD-9",
        "updated_fields": ["description", "summary"],
    }


def test_transition_list_json(fake_server, capsys):
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-7/transitions",
        response={"transitions": [{"id": "11", "name": "Open"}]},
    )
    main(["transition", "CBRD-7", "--output", "json"])
    out = _sole_stdout_json(capsys)
    assert out["issue"] == "CBRD-7"
    assert out["transitions"] == [{"id": "11", "name": "Open"}]


# --------------------------------------------------------------------------- #
# comment-list / comment-update / comment-delete
# --------------------------------------------------------------------------- #

def test_comment_list_live_json_shape(fake_server, capsys):
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-5/comment",
        response={
            "comments": [{
                "id": "1001",
                "author": {"displayName": "vimkim"},
                "created": "2025-01-02T11:22:33.000+0000",
                "body": "hello",
            }],
            "total": 1,
        },
    )
    main(["comment-list", "CBRD-5", "--output", "json"])
    body = _sole_stdout_json(capsys)
    assert body["issue"] == "CBRD-5"
    assert body["total"] == 1
    assert body["comments"] == [{
        "id": "1001",
        "author": "vimkim",
        "created": "2025-01-02T11:22:33.000+0000",
        "body": "hello",
    }]


def test_comment_update_live_json_shape(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "PUT", "/rest/api/2/issue/CBRD-5/comment/1001",
        response={"id": "1001", "body": "edited"},
    )
    body_file = tmp_path / "b.md"
    body_file.write_text("edited")
    main([
        "comment-update", "CBRD-5",
        "--id", "1001",
        "--body-file", str(body_file),
        "--yes", "--output", "json",
    ])
    assert _sole_stdout_json(capsys) == {
        "issue": "CBRD-5", "comment_id": "1001", "updated": True,
    }


def test_comment_delete_live_json_shape(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    fake_server.route(
        "DELETE", "/rest/api/2/issue/CBRD-5/comment/1001",
        response=None,
    )
    main([
        "comment-delete", "CBRD-5",
        "--id", "1001",
        "--yes", "--output", "json",
    ])
    assert _sole_stdout_json(capsys) == {
        "issue": "CBRD-5", "comment_id": "1001", "deleted": True,
    }


def test_comment_delete_dry_run_json_captures_plan(fake_server, capsys):
    main([
        "comment-delete", "CBRD-5",
        "--id", "1001",
        "--output", "json",
    ])
    plan = _sole_stdout_json(capsys)
    assert plan["dry_run"] is True
    assert len(plan["requests"]) == 1
    req = plan["requests"][0]
    assert req["method"] == "DELETE"
    assert req["url"].endswith("/rest/api/2/issue/CBRD-5/comment/1001")
    assert fake_server.requests == []
