"""Reads authenticate when a credential is available (approach A).

Background: ``docs/authenticated-reads-for-nonpublic-projects.md``. The read
helpers (``fetch_issue`` / ``search_issues``) used to always GET anonymously,
so login-required projects like ``CUBRIDQA`` returned 401/empty even with a
valid credential on disk. These tests pin the fixed behavior:

  * with a credential, reads send ``Authorization: Basic …``;
  * without one, reads stay anonymous (public-project behavior unchanged);
  * a 401 on an authenticated read exits 2 and does NOT retry (CAPTCHA
    footgun — see CLAUDE.md write-safety rule);
  * cache-first ``search`` still skips the network on a cache-only hit.
"""

from __future__ import annotations

import pytest
from conftest import make_http_error

from cubrid_jira import http
from cubrid_jira.cli import main
from cubrid_jira.http import fetch_issue, search_issues
from cubrid_jira.walk import fetch_recursive


def _auth_of(headers: dict) -> str | None:
    for k, v in headers.items():
        if k.lower() == "authorization":
            return v
    return None


# --------------------------------------------------------------------------- #
# Credential present → reads authenticate. (conftest sets CUBRID_JIRA_USER /
# _PASSWORD for every test, so creds resolve by default.)
# --------------------------------------------------------------------------- #

def test_search_issues_sends_auth_when_creds_present(fake_server):
    fake_server.route(
        "GET", "",
        response={"total": 1, "issues": [{"key": "CUBRIDQA-1425"}]},
    )
    result = search_issues("key = CUBRIDQA-1425")
    assert result["issues"][0]["key"] == "CUBRIDQA-1425"
    assert _auth_of(fake_server.requests[-1].headers) is not None


def test_fetch_issue_sends_auth_when_creds_present(fake_server):
    fake_server.route(
        "GET", "/rest/api/2/issue/CUBRIDQA-1425?expand=renderedFields",
        response={"key": "CUBRIDQA-1425", "fields": {"summary": "private"}},
    )
    data = fetch_issue("CUBRIDQA-1425")
    assert data.get("key") == "CUBRIDQA-1425"
    assert _auth_of(fake_server.requests[-1].headers) is not None


def test_walk_deep_fetch_authenticates(fake_server, tmp_path):
    # walk.py's recursive fetch must authenticate too, or private sub-trees
    # stay invisible. --no-recurse depth 0 still exercises the fetch path.
    fake_server.route(
        "GET", "/rest/api/2/issue/CUBRIDQA-1425?expand=renderedFields",
        response={"key": "CUBRIDQA-1425", "fields": {"summary": "private"}},
    )
    ok = fetch_recursive("CUBRIDQA-1425", 0, set(), tmp_path, force=True)
    assert ok
    assert _auth_of(fake_server.requests[-1].headers) is not None


# --------------------------------------------------------------------------- #
# No credential → reads stay anonymous. The real ~/.netrc on this machine has
# a jira.cubrid.org entry, so clearing env is not enough — patch the resolver
# so the test does not depend on the developer's netrc.
# --------------------------------------------------------------------------- #

def test_search_issues_stays_anonymous_without_creds(fake_server, monkeypatch):
    monkeypatch.setattr(http, "resolve_credentials_optional", lambda *a, **k: None)
    fake_server.route("GET", "", response={"total": 0, "issues": []})
    search_issues("project = CBRD")
    assert _auth_of(fake_server.requests[-1].headers) is None


def test_fetch_issue_stays_anonymous_without_creds(fake_server, monkeypatch):
    monkeypatch.setattr(http, "resolve_credentials_optional", lambda *a, **k: None)
    fake_server.route(
        "GET", "/rest/api/2/issue/CBRD-1?expand=renderedFields",
        response={"key": "CBRD-1", "fields": {"summary": "public"}},
    )
    fetch_issue("CBRD-1")
    assert _auth_of(fake_server.requests[-1].headers) is None


# --------------------------------------------------------------------------- #
# 401 on an authenticated read → exit 2, single attempt (no retry).
# --------------------------------------------------------------------------- #

def test_jql_authenticated_401_exits_2_without_retry(fake_server):
    fake_server.route("GET", "", raise_=make_http_error(401, "auth failed"))
    with pytest.raises(SystemExit) as ei:
        main(["jql", "key = CUBRIDQA-1425"])
    assert ei.value.code == 2
    # No retry on 401 — exactly one request went out.
    assert len(fake_server.requests) == 1


# --------------------------------------------------------------------------- #
# Cache-first search is unaffected: a cache-only hit still skips the network.
# --------------------------------------------------------------------------- #

def test_search_cache_only_still_skips_network(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-1.md").write_text("# cached only\n", encoding="utf-8")
    main(["search", "CBRD-1", "--cache-only"])
    out = capsys.readouterr().out
    assert "# cached only" in out
    assert fake_server.requests == []
