"""End-to-end CLI tests for convert-to-issue / convert-to-subtask / reparent.

These exercise:

* Pre-flight refusals (already-non-subtask, no-op self-reparent, sub-task
  as new parent, missing --to).
* Step ordering of the four wizard URLs.
* Header presence: X-Atlassian-Token: no-check on every wizard POST.
* Cookie continuity: a pre-populated CookieJar's value flows into the
  Cookie header on subsequent requests.
* Cache invalidation: 2 keys for convert-*, 3 keys for reparent.
* The dry-run plan shape (with placeholder atl_token / guid / issuetype).
* The reparent atomicity guard (loud warning if step 2 fails after step 1).
"""

from __future__ import annotations

import json
from http.cookiejar import Cookie

import pytest

from cubrid_jira.cli import main
from cubrid_jira.session import SessionClient


WIZARD_PAGE_HTML = """
<form name="jiraform" action="/secure/ConvertSubTaskSetIssueType.jspa">
  <input type="hidden" name="atl_token" value="TOK-1"/>
  <input type="hidden" name="guid" value="GUID-1"/>
  <select name="issuetype">
    <option value="10500">Task</option>
    <option value="3">Story</option>
  </select>
</form>
"""

WIZARD_PAGE_HTML_FOR_SUBTASK = """
<form name="jiraform" action="/secure/ConvertIssueSetIssueType.jspa">
  <input type="hidden" name="atl_token" value="TOK-REV"/>
  <input type="hidden" name="guid" value="GUID-REV"/>
  <select name="issuetype">
    <option value="5">Sub-task</option>
  </select>
</form>
"""

# A wizard step response that updates the atl_token, exercising the
# re-extraction codepath (Trap 5 in the doc).
STEP_RESPONSE_HTML = """
<form name="jiraform">
  <input type="hidden" name="atl_token" value="TOK-NEXT"/>
  <input type="hidden" name="guid" value="GUID-NEXT"/>
</form>
"""


# --------------------------------------------------------------------------- #
# Helpers — issue metadata fixtures
# --------------------------------------------------------------------------- #

def _meta_subtask(key: str, issue_id: str, parent_key: str | None = "CBRD-1") -> dict:
    fields: dict = {"issuetype": {"name": "Sub-task"}}
    if parent_key:
        fields["parent"] = {"key": parent_key}
    return {"id": issue_id, "key": key, "fields": fields}


def _meta_task(key: str, issue_id: str) -> dict:
    return {"id": issue_id, "key": key, "fields": {"issuetype": {"name": "Task"}}}


def _route_meta(server, key: str, meta: dict) -> None:
    """Stub fetch_issue() for ``key``."""
    server.route(
        "GET",
        f"/rest/api/2/issue/{key}?expand=renderedFields",
        response=meta,
    )


def _route_wizard_warmup_and_login(server) -> None:
    """Stub the session-establishment endpoints used by SessionClient.login()."""
    server.route("GET", "/secure/Dashboard.jspa", response=b"")
    server.route("POST", "/rest/auth/1/session", response={"session": {"name": "JSESSIONID", "value": "X"}})


def _route_forward_wizard_steps(server, ok_html=STEP_RESPONSE_HTML) -> None:
    server.route("POST", "/secure/ConvertSubTaskSetIssueType.jspa", response=ok_html)
    server.route("POST", "/secure/ConvertSubTaskUpdateFields.jspa", response=ok_html)
    server.route("POST", "/secure/ConvertSubTaskConvert.jspa", response=ok_html)


def _route_reverse_wizard_steps(server, ok_html=STEP_RESPONSE_HTML) -> None:
    server.route("POST", "/secure/ConvertIssueSetIssueType.jspa", response=ok_html)
    server.route("POST", "/secure/ConvertIssueUpdateFields.jspa", response=ok_html)
    server.route("POST", "/secure/ConvertIssueConvert.jspa", response=ok_html)


# --------------------------------------------------------------------------- #
# Argparse / pre-flight refusals — no network needed.
# --------------------------------------------------------------------------- #

def test_convert_to_issue_rejects_non_subtask(fake_server, capsys):
    _route_meta(fake_server, "CBRD-9", _meta_task("CBRD-9", "9000"))
    with pytest.raises(SystemExit) as ei:
        main(["convert-to-issue", "CBRD-9", "--yes"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "Sub-task" in err and "Task" in err


def test_convert_to_subtask_rejects_subtask_source(fake_server, capsys):
    _route_meta(fake_server, "CBRD-9", _meta_subtask("CBRD-9", "9"))
    with pytest.raises(SystemExit) as ei:
        main(["convert-to-subtask", "CBRD-9", "--to", "CBRD-1", "--yes"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "already a Sub-task" in err


def test_convert_to_subtask_rejects_subtask_parent(fake_server, capsys):
    _route_meta(fake_server, "CBRD-9", _meta_task("CBRD-9", "9"))
    _route_meta(fake_server, "CBRD-2", _meta_subtask("CBRD-2", "2"))
    with pytest.raises(SystemExit) as ei:
        main(["convert-to-subtask", "CBRD-9", "--to", "CBRD-2", "--yes"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "Sub-task" in err and "non-subtask" in err


def test_reparent_rejects_self_noop(fake_server, capsys):
    """If the issue is already a sub-task of --to, reparent must refuse."""
    _route_meta(fake_server, "CBRD-9", _meta_subtask("CBRD-9", "9", parent_key="CBRD-1"))
    with pytest.raises(SystemExit) as ei:
        main(["reparent", "CBRD-9", "--to", "CBRD-1", "--yes"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "already" in err.lower() and "CBRD-1" in err


def test_reparent_rejects_subtask_as_new_parent(fake_server, capsys):
    _route_meta(fake_server, "CBRD-9", _meta_subtask("CBRD-9", "9", parent_key="CBRD-1"))
    _route_meta(fake_server, "CBRD-2", _meta_subtask("CBRD-2", "2"))
    with pytest.raises(SystemExit):
        main(["reparent", "CBRD-9", "--to", "CBRD-2", "--yes"])
    err = capsys.readouterr().err
    assert "Sub-task" in err and "non-subtask" in err


# --------------------------------------------------------------------------- #
# Dry-run plans — no live HTTP at all
# --------------------------------------------------------------------------- #

def test_convert_to_issue_dry_run_records_three_steps(fake_server, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    _route_meta(fake_server, "CBRD-9", _meta_subtask("CBRD-9", "9", parent_key="CBRD-1"))

    main(["convert-to-issue", "CBRD-9", "--output", "json"])

    plan = json.loads(capsys.readouterr().out.strip())
    assert plan["dry_run"] is True
    assert len(plan["requests"]) == 3
    urls = [r["url"] for r in plan["requests"]]
    assert urls[0].endswith("/secure/ConvertSubTaskSetIssueType.jspa")
    assert urls[1].endswith("/secure/ConvertSubTaskUpdateFields.jspa")
    assert urls[2].endswith("/secure/ConvertSubTaskConvert.jspa")
    # Step 1 plan uses placeholders, NOT a hard-coded numeric issuetype id.
    assert plan["requests"][0]["body"]["issuetype"] == "<resolved-at-runtime>"
    assert plan["requests"][0]["body"]["atl_token"] == "<extracted-at-runtime>"
    # The dry-run did exactly one read (the metadata fetch); no wizard or login traffic.
    assert [r.url for r in fake_server.requests] == [
        "http://jira.cubrid.org/rest/api/2/issue/CBRD-9?expand=renderedFields",
    ]


def test_reparent_dry_run_records_six_steps(fake_server, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    _route_meta(fake_server, "CBRD-9", _meta_subtask("CBRD-9", "9", parent_key="CBRD-1"))
    _route_meta(fake_server, "CBRD-2", _meta_task("CBRD-2", "2"))

    main(["reparent", "CBRD-9", "--to", "CBRD-2", "--output", "json"])

    plan = json.loads(capsys.readouterr().out.strip())
    assert plan["dry_run"] is True
    assert len(plan["requests"]) == 6  # 3 forward + 3 reverse
    urls = [r["url"] for r in plan["requests"]]
    assert urls[:3] == [
        "http://jira.cubrid.org/secure/ConvertSubTaskSetIssueType.jspa",
        "http://jira.cubrid.org/secure/ConvertSubTaskUpdateFields.jspa",
        "http://jira.cubrid.org/secure/ConvertSubTaskConvert.jspa",
    ]
    assert urls[3:] == [
        "http://jira.cubrid.org/secure/ConvertIssueSetIssueType.jspa",
        "http://jira.cubrid.org/secure/ConvertIssueUpdateFields.jspa",
        "http://jira.cubrid.org/secure/ConvertIssueConvert.jspa",
    ]
    # Reverse Step 1 must include parentIssueKey = new parent.
    assert plan["requests"][3]["body"]["parentIssueKey"] == "CBRD-2"


# --------------------------------------------------------------------------- #
# Live wizard — step ordering, header presence, cache invalidation
# --------------------------------------------------------------------------- #

def _wizard_post_requests(fake_server) -> list:
    return [r for r in fake_server.requests if r.method == "POST" and "/secure/" in r.url]


def test_convert_to_issue_live_full_flow(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-9.md").write_text("stale")
    (tmp_path / "CBRD-1.md").write_text("old parent stale")

    _route_wizard_warmup_and_login(fake_server)
    # Pre + verify metadata (called twice — before and after).
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-9?expand=renderedFields",
        response=_meta_subtask("CBRD-9", "1418856", parent_key="CBRD-1"),
    )
    fake_server.route(
        "GET",
        f"{ '/secure/ConvertSubTask.jspa?id=1418856' }",
        response=WIZARD_PAGE_HTML,
    )
    _route_forward_wizard_steps(fake_server)

    # The verification GET re-uses the same suffix; first call sees the
    # "before" state, but FakeJiraServer matches by suffix and the matching
    # route returns the same canned response, so we re-route to "after"
    # by overriding after the wizard POSTs run. The cleanest path is to
    # only ever route once with the AFTER metadata — convert-to-issue's
    # pre-flight only inspects type=Sub-task, parent (whichever the route
    # returns), and post-flight expects type=Task, parent=None. So we
    # need two distinct routes... we cheat with two responses by re-routing
    # via a per-call list.

    # Simpler: stub a sequence using a state machine on a captured list.
    states = iter([
        _meta_subtask("CBRD-9", "1418856", parent_key="CBRD-1"),  # pre-flight
        _meta_task("CBRD-9", "1418856"),                          # post-flight (parent dropped)
    ])

    class _Sequenced:
        def __init__(self, server): self.server = server
        def __call__(self, req, timeout=None):
            url = req.full_url
            if url.endswith("/rest/api/2/issue/CBRD-9?expand=renderedFields"):
                # Pop the next canned meta response.
                payload = next(states)
                return _stub_response(payload)
            return self.server._real_urlopen(req, timeout=timeout)

    # Replace fake_server.urlopen with a thin wrapper that handles the
    # two-state metadata route specially.
    real = fake_server.urlopen

    def _wrapped(req, timeout=None):
        url = req.full_url
        if url.endswith("/rest/api/2/issue/CBRD-9?expand=renderedFields"):
            payload = next(states)
            fake_server.requests.append(_RecorderSentinel(req, payload))
            return _stub_response(payload)
        return real(req, timeout=timeout)

    monkeypatch.setattr("urllib.request.urlopen", _wrapped)

    main(["convert-to-issue", "CBRD-9", "--yes"])

    # Three wizard POSTs in order.
    posts = _wizard_post_requests(fake_server)
    assert [r.url for r in posts] == [
        "http://jira.cubrid.org/secure/ConvertSubTaskSetIssueType.jspa",
        "http://jira.cubrid.org/secure/ConvertSubTaskUpdateFields.jspa",
        "http://jira.cubrid.org/secure/ConvertSubTaskConvert.jspa",
    ]
    # Every wizard POST must carry X-Atlassian-Token: no-check.
    for p in posts:
        assert p.headers.get("X-atlassian-token") == "no-check"
    # Cache invalidation for BOTH the issue and its previous parent.
    assert not (tmp_path / "CBRD-9.md").exists()
    assert not (tmp_path / "CBRD-1.md").exists()


def test_reparent_live_full_flow(fake_server, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-9.md").write_text("stale")
    (tmp_path / "CBRD-1.md").write_text("old parent stale")
    (tmp_path / "CBRD-2.md").write_text("new parent stale")

    _route_wizard_warmup_and_login(fake_server)
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-2?expand=renderedFields",
        response=_meta_task("CBRD-2", "200"),
    )
    fake_server.route("GET", "/secure/ConvertSubTask.jspa?id=99",
                      response=WIZARD_PAGE_HTML)
    fake_server.route("GET", "/secure/ConvertIssue.jspa?id=99",
                      response=WIZARD_PAGE_HTML_FOR_SUBTASK)
    _route_forward_wizard_steps(fake_server)
    _route_reverse_wizard_steps(fake_server)

    # 3-state metadata: pre, intermediate (after step 1), final (after step 2).
    states = iter([
        _meta_subtask("CBRD-9", "99", parent_key="CBRD-1"),  # pre
        _meta_task("CBRD-9", "99"),                          # intermediate
        {                                                     # final
            "id": "99", "key": "CBRD-9",
            "fields": {
                "issuetype": {"name": "Sub-task"},
                "parent": {"key": "CBRD-2"},
            },
        },
    ])
    real = fake_server.urlopen

    def _wrapped(req, timeout=None):
        url = req.full_url
        if url.endswith("/rest/api/2/issue/CBRD-9?expand=renderedFields"):
            payload = next(states)
            fake_server.requests.append(_RecorderSentinel(req, payload))
            return _stub_response(payload)
        return real(req, timeout=timeout)

    monkeypatch.setattr("urllib.request.urlopen", _wrapped)

    main(["reparent", "CBRD-9", "--to", "CBRD-2", "--yes"])

    # Six wizard POSTs in order — forward then reverse.
    posts = _wizard_post_requests(fake_server)
    assert [r.url.rsplit("/", 1)[-1] for r in posts] == [
        "ConvertSubTaskSetIssueType.jspa",
        "ConvertSubTaskUpdateFields.jspa",
        "ConvertSubTaskConvert.jspa",
        "ConvertIssueSetIssueType.jspa",
        "ConvertIssueUpdateFields.jspa",
        "ConvertIssueConvert.jspa",
    ]
    # Reverse Step 1 carries the new parentIssueKey.
    rev_step1 = posts[3]
    body = dict(_parse_form_body(rev_step1.body))
    assert body["parentIssueKey"] == "CBRD-2"
    # All three cache files were invalidated.
    assert not (tmp_path / "CBRD-9.md").exists()
    assert not (tmp_path / "CBRD-1.md").exists()
    assert not (tmp_path / "CBRD-2.md").exists()


def test_reparent_atomicity_warning_when_reverse_fails(
    fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    (tmp_path / "CBRD-9.md").write_text("stale")

    _route_wizard_warmup_and_login(fake_server)
    fake_server.route(
        "GET",
        "/rest/api/2/issue/CBRD-2?expand=renderedFields",
        response=_meta_task("CBRD-2", "200"),
    )
    fake_server.route("GET", "/secure/ConvertSubTask.jspa?id=99",
                      response=WIZARD_PAGE_HTML)
    # Reverse wizard GET returns the XSRF rejection page — final state
    # never reached, atomicity guard must fire.
    fake_server.route(
        "GET", "/secure/ConvertIssue.jspa?id=99",
        response="<title>XSRF Security Token Missing</title>",
    )
    _route_forward_wizard_steps(fake_server)

    states = iter([
        _meta_subtask("CBRD-9", "99", parent_key="CBRD-1"),  # pre
        _meta_task("CBRD-9", "99"),                          # intermediate ok
    ])
    real = fake_server.urlopen

    def _wrapped(req, timeout=None):
        url = req.full_url
        if url.endswith("/rest/api/2/issue/CBRD-9?expand=renderedFields"):
            payload = next(states)
            fake_server.requests.append(_RecorderSentinel(req, payload))
            return _stub_response(payload)
        return real(req, timeout=timeout)

    monkeypatch.setattr("urllib.request.urlopen", _wrapped)

    with pytest.raises(SystemExit) as ei:
        main(["reparent", "CBRD-9", "--to", "CBRD-2", "--yes"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    # The loud warning must mention the stranded state AND the recovery path.
    assert "ATOMICITY WARNING" in err
    assert "Task with no parent" in err
    assert "convert-to-subtask CBRD-9 --to CBRD-2" in err


# --------------------------------------------------------------------------- #
# SessionClient unit tests — header + cookie behavior
# --------------------------------------------------------------------------- #

def _add_cookie(jar, name, value):
    jar.set_cookie(Cookie(
        version=0, name=name, value=value,
        port=None, port_specified=False,
        domain="jira.cubrid.org", domain_specified=True, domain_initial_dot=False,
        path="/", path_specified=True,
        secure=False, expires=None, discard=True,
        comment=None, comment_url=None,
        rest={}, rfc2109=False,
    ))


def test_session_client_post_adds_xsrf_header_and_cookie(fake_server):
    """Sanity: html_post adds X-Atlassian-Token AND attaches any jar cookies."""
    client = SessionClient("http://jira.cubrid.org", "u", "p")
    _add_cookie(client.cookies, "JSESSIONID", "ABC123")

    fake_server.route(
        "POST", "/secure/ConvertSubTaskSetIssueType.jspa",
        response=STEP_RESPONSE_HTML,
    )
    client.html_post(
        "/secure/ConvertSubTaskSetIssueType.jspa",
        {"id": "1", "atl_token": "T", "guid": "G",
         "issuetype": "10500", "Next >>": "Next >>"},
    )
    rec = fake_server.requests[0]
    # X-Atlassian-Token is essential — without it the commit step 4 returns
    # an HTML "XSRF Security Token Missing" page even with a valid form token.
    assert rec.headers.get("X-atlassian-token") == "no-check"
    assert rec.headers.get("Content-type") == "application/x-www-form-urlencoded"
    # Cookie continuity — the JSESSIONID we put in the jar must flow.
    cookie_hdr = rec.headers.get("Cookie") or rec.headers.get("cookie")
    assert cookie_hdr and "JSESSIONID=ABC123" in cookie_hdr


def test_session_client_dry_run_records_no_send(fake_server):
    client = SessionClient(
        "http://jira.cubrid.org", "u", "p", dry_run=True
    )
    out = client.html_post("/secure/X.jspa", {"id": "1"})
    assert out == ""
    assert fake_server.requests == []
    assert client.recorded_requests == [
        {"method": "POST", "url": "http://jira.cubrid.org/secure/X.jspa", "body": {"id": "1"}},
    ]


# --------------------------------------------------------------------------- #
# Test helpers (kept local to this file).
# --------------------------------------------------------------------------- #

from dataclasses import dataclass
import urllib.parse


def _parse_form_body(body: bytes) -> list[tuple[str, str]]:
    return urllib.parse.parse_qsl(body.decode("utf-8"), keep_blank_values=True)


class _RecorderSentinel:
    """Compat shim — a tuple of (method, url, headers, body) for assertions."""
    __slots__ = ("method", "url", "headers", "body")

    def __init__(self, req, _payload):
        self.method = req.get_method().upper()
        self.url = req.full_url
        merged = dict(req.headers)
        merged.update(req.unredirected_hdrs)
        self.headers = merged
        self.body = req.data


def _stub_response(payload):
    """Build a tiny _FakeResponse-like object the SessionClient + fetch_issue
    can read from.
    """
    import io
    import json as _json

    class _R:
        def __init__(self, raw):
            self._raw = raw

        def read(self):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    if isinstance(payload, (dict, list)):
        return _R(_json.dumps(payload).encode("utf-8"))
    if isinstance(payload, bytes):
        return _R(payload)
    return _R(str(payload).encode("utf-8"))
