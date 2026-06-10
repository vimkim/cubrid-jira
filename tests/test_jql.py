"""jql subcommand: pure markdown rendering + --output json contract.

Network is never hit here — the renderer is pure and the CLI test
monkeypatches ``search_issues`` with a canned response. The live read of the
real ``/rest/api/2/search`` endpoint lives in ``test_live.py`` under
``-m live``.
"""

from __future__ import annotations

import json
import urllib.error

import pytest
from conftest import make_http_error

import cubrid_jira.cli as cli
from cubrid_jira.cli import main
from cubrid_jira.http import JiraError, search_issues
from cubrid_jira.markdown import format_search_results_markdown

_SAMPLE = {
    "total": 2,
    "issues": [
        {
            "key": "CBRD-100",
            "fields": {
                "summary": "Fix a | piped thing",
                "status": {"name": "Develop"},
                "issuetype": {"name": "Bug"},
                "assignee": {"displayName": "Il Han, Song"},
                "updated": "2026-06-02T11:22:33.000+0900",
            },
        },
        {
            "key": "CBRD-101",
            "fields": {
                "summary": "Unassigned thing",
                "status": {"name": "Confirmed"},
                "issuetype": {"name": "Task"},
                "assignee": None,
                "updated": "2026-06-01T00:00:00.000+0900",
            },
        },
    ],
}


def test_render_table_has_header_and_rows():
    md = format_search_results_markdown(_SAMPLE)
    assert "2 of 2 matching issues" in md
    assert "| Key | Status | Type | Assignee | Updated | Summary |" in md
    # Linked key, status, truncated date, escaped pipe in summary.
    assert "[CBRD-100](http://jira.cubrid.org/browse/CBRD-100)" in md
    assert "| Develop |" in md
    assert "| 2026-06-02 |" in md
    assert "Fix a \\| piped thing" in md
    # Missing assignee falls back to "Unassigned".
    assert "| Unassigned |" in md


def test_render_empty_result():
    md = format_search_results_markdown({"total": 0, "issues": []})
    assert md == "# JQL search — 0 of 0 matching issues"
    # No table when there are no rows.
    assert "| Key |" not in md


def test_jql_output_json_is_single_line_raw_response(monkeypatch, capsys):
    captured = {}

    def fake_search(jql, fields="", max_results=50, start_at=0):
        captured["args"] = (jql, fields, max_results, start_at)
        return _SAMPLE

    monkeypatch.setattr(cli, "search_issues", fake_search)
    main(["jql", "assignee = jdoe", "--output", "json", "--max", "10"])

    out = capsys.readouterr().out.strip()
    assert "\n" not in out, f"expected ONE JSON line, got:\n{out!r}"
    assert json.loads(out) == _SAMPLE
    # CLI threaded the args through to search_issues.
    assert captured["args"][0] == "assignee = jdoe"
    assert captured["args"][2] == 10


def test_jql_text_output_renders_table(monkeypatch, capsys):
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _SAMPLE)
    main(["jql", "project = CBRD"])
    out = capsys.readouterr().out
    assert "matching issues" in out
    assert "| Key | Status | Type | Assignee | Updated | Summary |" in out


# --------------------------------------------------------------------------- #
# search_issues — real URL construction + error mapping (via fake_server).
# fake_server matches routes by url.endswith(suffix); the search URL carries a
# query string, so these route on the empty suffix ("" matches any GET) and
# assert on the recorded request URL instead.
# --------------------------------------------------------------------------- #

def test_search_issues_builds_query_and_parses(fake_server):
    fake_server.route("GET", "", response={"total": 0, "issues": []})
    result = search_issues(
        "assignee = jdoe AND x = 1",
        fields="summary,status",
        max_results=10,
        start_at=20,
    )
    assert result == {"total": 0, "issues": []}
    url = fake_server.requests[-1].url
    assert "/rest/api/2/search?" in url
    # JQL is URL-encoded ('=' → %3D, spaces → '+'), not passed raw.
    assert "jql=assignee" in url and "%3D" in url
    assert "fields=summary%2Cstatus" in url
    assert "maxResults=10" in url
    assert "startAt=20" in url


def test_search_issues_http_400_raises_with_code(fake_server):
    fake_server.route(
        "GET", "",
        raise_=make_http_error(400, '{"errorMessages":["bad jql"]}'),
    )
    with pytest.raises(JiraError) as ei:
        search_issues("this is not valid")
    assert ei.value.code == 400
    assert "400" in str(ei.value)


def test_search_issues_network_error_raises_without_code(fake_server):
    # _fast_retries (autouse) no-ops the backoff sleep; one retry then raise.
    fake_server.route("GET", "", raise_=urllib.error.URLError("boom"))
    with pytest.raises(JiraError) as ei:
        search_issues("project = CBRD")
    assert ei.value.code is None


def test_cmd_jql_maps_http_400_to_exit_5(fake_server):
    fake_server.route(
        "GET", "",
        raise_=make_http_error(400, "bad jql"),
    )
    with pytest.raises(SystemExit) as ei:
        main(["jql", "this is not valid"])
    assert ei.value.code == 5  # 400 → exit 5, per the project contract


def test_cmd_jql_text_unions_display_fields(fake_server):
    # A narrowed --fields must not blank the text table: the request still
    # includes the display columns.
    fake_server.route("GET", "", response={"total": 0, "issues": []})
    main(["jql", "project = CBRD", "--fields", "summary"])
    url = fake_server.requests[-1].url
    for col in ("summary", "status", "issuetype", "assignee", "updated"):
        assert col in url


def test_cmd_jql_json_keeps_fields_verbatim(fake_server):
    # JSON output honors --fields exactly — no display-column union.
    fake_server.route("GET", "", response={"total": 0, "issues": []})
    main(["jql", "project = CBRD", "--fields", "summary", "--output", "json"])
    url = fake_server.requests[-1].url
    assert "fields=summary&maxResults" in url


def test_cmd_jql_rejects_negative_max(capsys):
    with pytest.raises(SystemExit):
        main(["jql", "project = CBRD", "--max", "-1"])
