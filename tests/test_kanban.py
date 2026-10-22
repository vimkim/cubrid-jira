"""Personal board: complete pagination, status grouping, and output contracts."""

from __future__ import annotations

import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from rich.console import Console

from cubrid_jira import auth, kanban
from cubrid_jira.http import JiraError
from cubrid_jira.markdown import format_kanban_markdown


def issue(key, status="Develop", summary="[OOS] Korean 한글 | detail\nnext line"):
    return {
        "key": key,
        "fields": {
            "status": {"name": status},
            "issuetype": {"name": "Task"},
            "summary": summary,
            "updated": "2026-10-22T16:00:00.000+0900",
        },
    }


def test_fetches_all_pages_when_server_caps_page_size(monkeypatch):
    tickets = [issue(f"CBRD-{n}") for n in range(5)]
    calls = []

    def search(jql, **kwargs):
        calls.append((jql, kwargs))
        start = kwargs["start_at"]
        return {
            "startAt": start, "maxResults": 2, "total": 5,
            "issues": tickets[start:start + 2],
        }

    monkeypatch.setattr(kanban, "search_issues", search)
    assert kanban.fetch_my_active_tickets(server="http://jira.cubrid.com", page_size=100) == tickets
    assert [kwargs["start_at"] for _, kwargs in calls] == [0, 2, 4]
    for jql, kwargs in calls:
        assert "assignee = currentUser()" in jql
        assert "status NOT IN (Closed, Resolved)" in jql
        assert kwargs["server"] == "http://jira.cubrid.com"
        assert kwargs["max_results"] == 100


@pytest.mark.parametrize("second_page, message", [
    ({"startAt": 1, "total": 2, "issues": []}, "empty page"),
    ({"startAt": 1, "total": 2, "issues": [issue("CBRD-1")]}, "duplicate"),
    ({"startAt": 1, "total": 3, "issues": [issue("CBRD-2")]}, "changed"),
    ({"startAt": 0, "total": 2, "issues": [issue("CBRD-2")]}, "changed"),
])
def test_rejects_incomplete_or_inconsistent_pagination(monkeypatch, second_page, message):
    pages = iter([
        {"startAt": 0, "total": 2, "issues": [issue("CBRD-1")]},
        second_page,
    ])
    monkeypatch.setattr(kanban, "search_issues", lambda *args, **kwargs: next(pages))
    with pytest.raises(JiraError, match=message):
        kanban.fetch_my_active_tickets()


def test_groups_in_workflow_order_and_preserves_order_within_status():
    tickets = [
        issue("CBRD-1", "Test"), issue("CBRD-2", "Develop"),
        issue("CBRD-3", "Open"), issue("CBRD-4", "Develop"),
        issue("CBRD-5", "Handover"), issue("CBRD-6", "Confirmed"),
        issue("CBRD-7", "Waiting"), issue("CBRD-8", "Blocked"),
    ]
    groups = kanban.group_by_status(tickets)
    assert [status for status, _ in groups] == [
        "Open", "Confirmed", "Develop", "Test", "Handover", "Blocked", "Waiting",
    ]
    assert [ticket["key"] for ticket in dict(groups)["Develop"]] == ["CBRD-2", "CBRD-4"]
    assert kanban.group_by_status([{"key": "CBRD-9", "fields": {}}])[0][0] == "Unknown"


def test_terminal_view_keeps_literal_markup_and_wraps_korean():
    stream = io.StringIO()
    console = Console(file=stream, width=100, force_terminal=False, color_system=None)
    groups = [("Develop", [issue("CBRD-1", summary="[red]literal[/red] 한글 " + "요약 " * 40)])]
    kanban.print_kanban(groups, console=console)
    output = stream.getvalue()
    assert "Develop (1)" in output
    assert "CBRD-1" in output
    assert "[red]literal[/red]" in output
    assert "한글" in output
    assert output.count("요약") == 40  # wrapping keeps the complete summary
    assert "\x1b" not in output


def test_markdown_has_group_counts_links_and_escaped_cells():
    markdown = format_kanban_markdown(
        [("Develop", [issue("CBRD-1")])], server="http://jira.cubrid.com/",
    )
    assert "# My active JIRA tickets (1)" in markdown
    assert "## Develop (1)" in markdown
    assert "[CBRD-1](http://jira.cubrid.com/browse/CBRD-1)" in markdown
    assert "한글 \\| detail next line" in markdown
    assert "| 2026-10-22 |" in markdown


def test_narrow_terminal_gives_summary_more_room():
    stream = io.StringIO()
    console = Console(file=stream, width=80, force_terminal=False)
    kanban.print_kanban([("Open", [issue("CBRD-1", "Open")])], console=console)
    output = stream.getvalue()
    assert "Type" not in output
    assert "Summary" in output
    assert "CBRD-1" in output


def test_json_cli_preserves_issues_and_uses_selected_server(fake_server, capsys):
    tickets = [issue("RND-1", "Develop"), issue("RND-2", "Open")]
    fake_server.route("GET", "", response={
        "startAt": 0, "total": 2, "issues": tickets,
    })
    kanban.main(["--output", "json", "--server", "http://jira.cubrid.com"])
    output = capsys.readouterr()
    assert output.err == ""
    assert len(output.out.splitlines()) == 1
    result = json.loads(output.out)
    assert result["total"] == 2
    assert [group["status"] for group in result["groups"]] == ["Open", "Develop"]
    assert result["groups"][1]["issues"] == [tickets[0]]
    request = fake_server.requests[0]
    assert request.url.startswith("http://jira.cubrid.com/rest/api/2/search?")
    assert request.headers["Authorization"].startswith("Basic ")
    assert parse_qs(urlsplit(request.url).query)["jql"] == [kanban.MY_ACTIVE_JQL]


@pytest.mark.parametrize("code, exit_code", [(401, 2), (403, 3), (400, 5), (None, 1)])
def test_failure_on_later_page_prints_no_partial_board(monkeypatch, capsys, code, exit_code):
    calls = []

    def search(*args, **kwargs):
        calls.append(kwargs["start_at"])
        if len(calls) == 1:
            return {"startAt": 0, "total": 2, "issues": [issue("CBRD-1")]}
        raise JiraError("request failed", code=code)

    monkeypatch.setattr(kanban, "search_issues", search)
    with pytest.raises(SystemExit) as error:
        kanban.main([])
    assert error.value.code == exit_code
    output = capsys.readouterr()
    assert output.out == ""
    assert "request failed" in output.err
    assert calls == [0, 1]


def test_missing_credentials_fails_before_any_request(monkeypatch, capsys):
    monkeypatch.setattr(auth, "resolve_credentials_optional", lambda server: None)
    monkeypatch.setattr(kanban, "search_issues", lambda *a, **kw: pytest.fail("unexpected request"))
    with pytest.raises(SystemExit) as error:
        kanban.main([])
    assert error.value.code == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("output", ["text", "markdown", "json"])
def test_empty_board_is_success(monkeypatch, capsys, output):
    monkeypatch.setattr(kanban, "search_issues", lambda *args, **kwargs: {"total": 0, "issues": []})
    kanban.main(["--output", output])
    captured = capsys.readouterr()
    assert captured.err == ""
    if output == "json":
        assert json.loads(captured.out) == {"total": 0, "groups": []}
    else:
        assert "No active tickets assigned to you." in captured.out


@pytest.mark.parametrize("page_size", ["0", "-1"])
def test_invalid_page_size_fails_before_request(monkeypatch, page_size):
    monkeypatch.setattr(kanban, "search_issues", lambda *a, **kw: pytest.fail("unexpected request"))
    with pytest.raises(SystemExit) as error:
        kanban.main(["--page-size", page_size])
    assert error.value.code == 2
