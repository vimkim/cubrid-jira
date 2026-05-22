"""JiraClient tests — dry-run, 401 hard-fail, 5xx single retry."""

from __future__ import annotations

import pytest

from cubrid_jira_fetcher.client import JiraClient, basic_auth_header

from conftest import make_http_error


def test_basic_auth_header():
    # echo -n 'u:p' | base64  →  dTpw
    assert basic_auth_header("u", "p") == "Basic dTpw"


def test_dry_run_skips_mutating_request(fake_server, capsys):
    client = JiraClient("http://jira.cubrid.org", "u", "p", dry_run=True)
    result = client.request("POST", "/rest/api/2/issue", body={"hello": "world"})
    assert result is None
    # No network call was attempted.
    assert fake_server.requests == []
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.err
    assert "POST http://jira.cubrid.org/rest/api/2/issue" in captured.err
    # Body goes to stdout so it can be piped.
    assert '"hello": "world"' in captured.out
    # Authorization header must be masked.
    assert "***" in captured.err
    assert "dTpw" not in captured.err


def test_dry_run_lets_get_through(fake_server):
    fake_server.route("GET", "/rest/api/2/issue/CBRD-1/transitions",
                      response={"transitions": []})
    client = JiraClient("http://jira.cubrid.org", "u", "p", dry_run=True)
    out = client.request("GET", "/rest/api/2/issue/CBRD-1/transitions")
    assert out == {"transitions": []}
    assert len(fake_server.requests) == 1


def test_401_hard_fail_no_retry(fake_server, capsys):
    fake_server.route("POST", "/rest/api/2/issue", raise_=make_http_error(401, "denied"))
    client = JiraClient("http://jira.cubrid.org", "u", "wrong")
    with pytest.raises(SystemExit) as ei:
        client.request("POST", "/rest/api/2/issue", body={"x": 1})
    assert ei.value.code == 2
    # Critically: only ONE attempt — never retry on 401.
    assert len(fake_server.requests) == 1
    err = capsys.readouterr().err
    assert "CAPTCHA" in err
    assert "Do NOT retry" in err


def test_403_hard_fail(fake_server, capsys):
    fake_server.route("POST", "/rest/api/2/issue",
                      raise_=make_http_error(403, "no perm"))
    client = JiraClient("http://jira.cubrid.org", "u", "p")
    with pytest.raises(SystemExit) as ei:
        client.request("POST", "/rest/api/2/issue", body={})
    assert ei.value.code == 3
    assert len(fake_server.requests) == 1


def test_400_prints_server_body(fake_server, capsys):
    body = '{"errorMessages":[],"errors":{"summary":"is required"}}'
    fake_server.route("POST", "/rest/api/2/issue",
                      raise_=make_http_error(400, body))
    client = JiraClient("http://jira.cubrid.org", "u", "p")
    with pytest.raises(SystemExit) as ei:
        client.request("POST", "/rest/api/2/issue", body={})
    assert ei.value.code == 5
    err = capsys.readouterr().err
    assert "summary" in err and "is required" in err


def test_5xx_retries_once_then_fails(fake_server):
    # Both attempts fail → we expect exactly TWO requests recorded.
    fake_server.route("POST", "/rest/api/2/issue",
                      raise_=make_http_error(503, "down"))
    client = JiraClient("http://jira.cubrid.org", "u", "p")
    with pytest.raises(SystemExit):
        client.request("POST", "/rest/api/2/issue", body={})
    assert len(fake_server.requests) == 2


def test_post_includes_basic_auth_header(fake_server):
    fake_server.route("POST", "/rest/api/2/issue", response={"key": "CBRD-1"})
    client = JiraClient("http://jira.cubrid.org", "u", "p")
    out = client.request("POST", "/rest/api/2/issue", body={"x": 1})
    assert out == {"key": "CBRD-1"}
    rec = fake_server.requests[0]
    # urllib lowercases header keys when stored on the Request object
    auth_value = rec.headers.get("Authorization") or rec.headers.get("authorization")
    assert auth_value == "Basic dTpw"
    assert rec.body == b'{\n  "x": 1\n}'
