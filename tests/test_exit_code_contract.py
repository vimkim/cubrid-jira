"""Failures end with an `Error:` line and the documented exit code.

0 ok | 1 generic | 2 HTTP 401 | 3 HTTP 403 | 4 HTTP 404 | 5 HTTP 400.
Scripts and agents branch on the code, so no failure may escape as a
traceback or collapse into the wrong code.
"""

from __future__ import annotations

import sys

import pytest

from conftest import make_http_error
from cubrid_jira.cli import main
from cubrid_jira.walk import bulk_fetch_main


@pytest.mark.parametrize(
    "argv, bad",
    [
        (["comment", "CBRD-XXXXX", "--body-file", "note.md"], "CBRD-XXXXX"),
        (["link", "CBRD-1", "--type", "Relates", "--to", "nonsense"], "nonsense"),
        (["transition", "not-a-key"], "not-a-key"),
    ],
)
def test_unparseable_issue_key_exits_1_before_any_request(
    argv, bad, fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.md").write_text("body\n", encoding="utf-8")

    with pytest.raises(SystemExit) as ei:
        main(argv)

    err = capsys.readouterr().err
    assert ei.value.code == 1
    assert err.startswith("Error:")
    assert bad in err
    assert "Traceback" not in err
    assert fake_server.requests == []


def _issue(key: str, summary: str, links: tuple[str, ...] = ()) -> dict:
    return {
        "id": "100",
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": "Open"},
            "issuetype": {"name": "Sub-task"},
            "parent": {"key": "CBRD-1"},
            "issuelinks": [
                {"type": {"name": "Relates"}, "outwardIssue": {"key": k}} for k in links
            ],
        },
    }


def _route_issue(server, key: str, **kw) -> None:
    server.route("GET", f"/rest/api/2/issue/{key}?expand=renderedFields", **kw)


@pytest.mark.parametrize("status, code", [(403, 3), (404, 4)])
def test_search_maps_the_requested_issue_http_status(
    status, code, fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    _route_issue(fake_server, "CBRD-7", raise_=make_http_error(status))

    with pytest.raises(SystemExit) as ei:
        main(["search", "CBRD-7", "--no-recurse"])

    err = capsys.readouterr().err
    assert ei.value.code == code
    assert "Error:" in err and "CBRD-7" in err


def test_search_skips_a_missing_related_issue(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    root = _issue("CBRD-7", "root summary", links=("CBRD-8",))
    root["fields"].pop("parent")
    _route_issue(fake_server, "CBRD-7", response=root)
    _route_issue(fake_server, "CBRD-8", raise_=make_http_error(404))

    main(["search", "CBRD-7"])

    out = capsys.readouterr().out
    assert "root summary" in out
    assert (tmp_path / "CBRD-7.md").exists()
    assert not (tmp_path / "CBRD-8.md").exists()


@pytest.mark.parametrize("status, code", [(403, 3), (500, 1)])
def test_structural_preflight_maps_http_status(
    status, code, fake_server, capsys
):
    _route_issue(fake_server, "CBRD-9", raise_=make_http_error(status))

    with pytest.raises(SystemExit) as ei:
        main(["convert-to-issue", "CBRD-9"])

    assert ei.value.code == code
    assert "CBRD-9" in capsys.readouterr().err


def test_legacy_fetch_maps_a_missing_requested_issue_to_4(
    fake_server, tmp_path, monkeypatch
):
    _route_issue(fake_server, "CBRD-7", raise_=make_http_error(404))
    monkeypatch.setattr(
        sys, "argv", ["cubrid-jira-fetch", "CBRD-7", "-d", str(tmp_path), "--no-recurse"]
    )

    with pytest.raises(SystemExit) as ei:
        bulk_fetch_main()

    assert ei.value.code == 4
