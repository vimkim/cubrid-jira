"""`cubrid-jira lint`: find `{{`/`}}` markers that touch Hangul.

File targets are checked after the same conversion a write would apply;
issue targets are checked as stored on the server.
"""

from __future__ import annotations

import io
import json

import pytest

from cubrid_jira.cli import main

ISSUE_GET = "/rest/api/2/issue/CBRD-5?expand=renderedFields"
BROKEN = "* *{{REC_HOME}}이 아닌 행.*"


def _issue(description: str, comments: list[dict], total: int | None = None):
    return {
        "key": "CBRD-5",
        "fields": {
            "description": description,
            "comment": {
                "comments": comments,
                "total": len(comments) if total is None else total,
            },
        },
    }


def _comment(cid: str, body: str) -> dict:
    return {
        "id": cid,
        "author": {"displayName": "Someone"},
        "created": "2026-11-19T16:25:17.000+0900",
        "body": body,
    }


# ---- file targets ---------------------------------------------------------- #

def test_lint_jira_file_clean_after_spacing_fix(tmp_path, capsys):
    body = tmp_path / "body.jira"
    body.write_text(f"정상 {{{{ok}}}} 값\n{BROKEN}\n")

    with pytest.raises(SystemExit) as exc:
        main(["lint", str(body), "--from", "jira"])

    assert exc.value.code == 0
    assert "ok" in capsys.readouterr().out


def test_lint_file_reports_contact_left_by_conversion(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "cubrid_jira.cli.markdown_to_jira_body", lambda text: f"정상\n{BROKEN}\n",
    )
    body = tmp_path / "report.md"
    body.write_text("ignored")

    with pytest.raises(SystemExit) as exc:
        main(["lint", str(body)])

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert f"line 2, col 14: {BROKEN}" in out


def test_lint_file_fix_prints_converted_body(tmp_path, capsys):
    body = tmp_path / "body.jira"
    body.write_text(f"{BROKEN}\n")

    with pytest.raises(SystemExit) as exc:
        main(["lint", str(body), "--from", "jira", "--fix"])

    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == "* *{{REC_HOME}} 이 아닌 행.*\n"
    assert "ok" in captured.err


def test_lint_stdin_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{BROKEN}\n"))

    with pytest.raises(SystemExit) as exc:
        main(["lint", "-", "--from", "jira", "--fix", "--output", "json"])

    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "target": "-",
        "kind": "file",
        "ok": True,
        "problems": [],
        "body": "* *{{REC_HOME}} 이 아닌 행.*\n",
    }


def test_lint_missing_file_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["lint", str(tmp_path / "nope.md")])

    assert exc.value.code == 1
    assert "nope.md" in capsys.readouterr().err


# ---- issue targets --------------------------------------------------------- #

def test_lint_issue_reports_broken_comment(fake_server, capsys):
    fake_server.route(
        "GET", ISSUE_GET,
        response=_issue("설명 {{ok}} 값", [
            _comment("100", "정상"),
            _comment("4778780", f"앞줄\n{BROKEN}"),
        ]),
    )

    with pytest.raises(SystemExit) as exc:
        main(["lint", "CBRD-5"])

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "comment 4778780 (Someone, 2026-11-19): 1 problem" in captured.out
    assert f"line 2, col 14: {BROKEN}" in captured.out
    assert "description and 2 comments; 1 with problems" in captured.out
    assert "comment-update CBRD-5 --id 4778780 --from jira" in captured.err
    assert [r.method for r in fake_server.requests] == ["GET"]


def test_lint_issue_json_shape(fake_server, capsys):
    fake_server.route(
        "GET", ISSUE_GET,
        response=_issue(f"{{{{x}}}}값", [_comment("7", "정상")]),
    )

    with pytest.raises(SystemExit) as exc:
        main(["lint", "https://jira.cubrid.org/browse/CBRD-5", "--output", "json"])

    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "target": "CBRD-5",
        "kind": "issue",
        "ok": False,
        "checked": {"description": True, "comments": 1, "comments_total": 1},
        "problems": [{
            "location": "description",
            "line": 1,
            "column": 4,
            "marker": "}}",
            "text": "{{x}}값",
        }],
    }


def test_lint_issue_clean(fake_server, capsys):
    fake_server.route("GET", ISSUE_GET, response=_issue(None, []))

    with pytest.raises(SystemExit) as exc:
        main(["lint", "CBRD-5"])

    assert exc.value.code == 0
    assert "1 with problems" not in capsys.readouterr().out


def test_lint_issue_warns_when_comments_are_truncated(fake_server, capsys):
    fake_server.route(
        "GET", ISSUE_GET, response=_issue("", [_comment("1", "정상")], total=3),
    )

    with pytest.raises(SystemExit):
        main(["lint", "CBRD-5"])

    assert "checked 1 of 3 comments" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [["--fix"], ["--from", "jira"]])
def test_lint_issue_rejects_file_only_flags(fake_server, capsys, flag):
    with pytest.raises(SystemExit) as exc:
        main(["lint", "CBRD-5", *flag])

    assert exc.value.code == 2
    assert fake_server.requests == []
