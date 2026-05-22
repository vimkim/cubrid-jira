"""Reparent CUBRID JIRA sub-tasks via the Convert wizard's HTML form flow.

Walks each issue through the 4-step ConvertSubTask wizard (Sub-task -> Task,
clearing the parent), then the 4-step ConvertIssue wizard (Task -> Sub-task
with new parent). Halts immediately on any failure so we never leave an issue
stranded as a Task with no parent.

Designed for JIRA Server 7.7.1 where `parent` is not on the Edit screen and
PUT /rest/api/2/issue/{key} silently no-ops on it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


BASE = "http://jira.cubrid.org"
NEW_PARENT = "CBRD-26835"
TASK_ISSUETYPE_ID = "10500"
SUBTASK_ISSUETYPE_ID = "5"
KEYS = [
    "CBRD-26660", "CBRD-26817", "CBRD-26818", "CBRD-26824",
    "CBRD-26814", "CBRD-26813", "CBRD-26815",
]


def make_opener(jar: CookieJar) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPRedirectHandler(),
    )


def basic_auth_header(user: str, pw: str) -> str:
    import base64
    raw = f"{user}:{pw}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def rest_get(opener, path: str, user: str, pw: str) -> dict:
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization", basic_auth_header(user, pw))
    req.add_header("Accept", "application/json")
    with opener.open(req, timeout=20) as r:
        return json.loads(r.read())


def login_session(opener, user: str, pw: str) -> None:
    """Establish the JSESSIONID + atlassian.xsrf.token cookies."""
    body = json.dumps({"username": user, "password": pw}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/rest/auth/1/session", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with opener.open(req, timeout=20) as r:
        r.read()


def warm_session(opener) -> None:
    """Visit a page so JIRA hands us the JSESSIONID via Set-Cookie."""
    with opener.open(BASE + "/secure/Dashboard.jspa", timeout=20) as r:
        r.read()


def html_get(opener, path: str) -> str:
    with opener.open(BASE + path, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def html_post(opener, path: str, fields: dict[str, str]) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Atlassian-Token", "no-check")
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


_TOKEN_RE = re.compile(r'name="atl_token"\s+value="([^"]+)"')
_GUID_RE = re.compile(r'name="guid"\s+value="([^"]+)"')
_ACTION_RE = re.compile(r'<form[^>]+name="jiraform"[^>]*action="([^"]+)"')
_ACTION_RE2 = re.compile(r'<form[^>]+action="([^"]+)"[^>]*name="jiraform"')
_STEP_RE = re.compile(r"Step (\d+) of (\d+)")
_TITLE_RE = re.compile(r"<title>([^<]+)</title>")


def parse_form(html: str) -> dict:
    return {
        "atl_token": (_TOKEN_RE.search(html).group(1) if _TOKEN_RE.search(html) else None),
        "guid": (_GUID_RE.search(html).group(1) if _GUID_RE.search(html) else None),
        "action": (_ACTION_RE.search(html) or _ACTION_RE2.search(html) or [None, None])[1] if (_ACTION_RE.search(html) or _ACTION_RE2.search(html)) else None,
        "step": _STEP_RE.search(html).group(0) if _STEP_RE.search(html) else None,
        "title": _TITLE_RE.search(html).group(1) if _TITLE_RE.search(html) else None,
    }


def check_xsrf(html: str) -> None:
    if "XSRF Security Token Missing" in html:
        raise RuntimeError("XSRF rejected — atl_token stale or X-Atlassian-Token header missing")


def convert_subtask_to_task(opener, issue_id: str, key: str) -> None:
    html = html_get(opener, f"/secure/ConvertSubTask.jspa?id={issue_id}")
    form = parse_form(html)
    if not form["atl_token"]:
        raise RuntimeError(f"{key}: could not find atl_token on ConvertSubTask page")

    # Step 1: choose Task issuetype.
    _, html = html_post(opener, "/secure/ConvertSubTaskSetIssueType.jspa", {
        "id": issue_id,
        "guid": form["guid"],
        "atl_token": form["atl_token"],
        "issuetype": TASK_ISSUETYPE_ID,
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)
    if not form["atl_token"]:
        raise RuntimeError(f"{key}: lost atl_token after Step 1")

    # Step 3 (Step 2 of "Select Parent" is skipped on the Task path):
    # Pass through Update Fields.
    _, html = html_post(opener, "/secure/ConvertSubTaskUpdateFields.jspa", {
        "id": issue_id,
        "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)
    if not form["atl_token"]:
        raise RuntimeError(f"{key}: lost atl_token after Step 3")

    # Step 4: Finish (commits the conversion).
    _, html = html_post(opener, "/secure/ConvertSubTaskConvert.jspa", {
        "id": issue_id,
        "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Finish": "Finish",
    })
    check_xsrf(html)


def convert_task_to_subtask(opener, issue_id: str, key: str, parent_key: str) -> None:
    html = html_get(opener, f"/secure/ConvertIssue.jspa?id={issue_id}")
    form = parse_form(html)
    if not form["atl_token"]:
        raise RuntimeError(f"{key}: could not find atl_token on ConvertIssue page")

    # Step 1: choose Sub-task + parent key.
    _, html = html_post(opener, "/secure/ConvertIssueSetIssueType.jspa", {
        "id": issue_id,
        "guid": form["guid"],
        "atl_token": form["atl_token"],
        "issuetype": SUBTASK_ISSUETYPE_ID,
        "parentIssueKey": parent_key,
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)
    if not form["atl_token"]:
        raise RuntimeError(f"{key}: lost atl_token after reverse Step 1")

    # Step 3: pass through Update Fields.
    _, html = html_post(opener, "/secure/ConvertIssueUpdateFields.jspa", {
        "id": issue_id,
        "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)
    if not form["atl_token"]:
        raise RuntimeError(f"{key}: lost atl_token after reverse Step 3")

    # Step 4: Finish.
    _, html = html_post(opener, "/secure/ConvertIssueConvert.jspa", {
        "id": issue_id,
        "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Finish": "Finish",
    })
    check_xsrf(html)


def reparent_one(opener, key: str, user: str, pw: str, parent_key: str) -> None:
    meta = rest_get(opener, f"/rest/api/2/issue/{key}?fields=issuetype,parent", user, pw)
    issue_id = meta["id"]
    cur_type = (meta["fields"].get("issuetype") or {}).get("name")
    cur_parent = (meta["fields"].get("parent") or {}).get("key") or "(none)"
    print(f"[{key}] id={issue_id} before: type={cur_type} parent={cur_parent}")

    if cur_type != "Sub-task":
        raise RuntimeError(
            f"{key} is currently type={cur_type!r}, expected 'Sub-task'. "
            "Refusing to convert to avoid stranding the issue."
        )

    convert_subtask_to_task(opener, issue_id, key)

    # Verify intermediate state — must be Task with no parent.
    meta = rest_get(opener, f"/rest/api/2/issue/{key}?fields=issuetype,parent", user, pw)
    inter_type = (meta["fields"].get("issuetype") or {}).get("name")
    inter_parent = (meta["fields"].get("parent") or {}).get("key") or "(none)"
    print(f"[{key}] intermediate: type={inter_type} parent={inter_parent}")
    if inter_type != "Task" or inter_parent != "(none)":
        raise RuntimeError(
            f"{key}: forward conversion landed in unexpected state "
            f"type={inter_type} parent={inter_parent}. Halting."
        )

    convert_task_to_subtask(opener, issue_id, key, parent_key)

    # Verify final state.
    meta = rest_get(opener, f"/rest/api/2/issue/{key}?fields=issuetype,parent", user, pw)
    fin_type = (meta["fields"].get("issuetype") or {}).get("name")
    fin_parent = (meta["fields"].get("parent") or {}).get("key") or "(none)"
    print(f"[{key}] after: type={fin_type} parent={fin_parent}")
    if fin_type != "Sub-task" or fin_parent != parent_key:
        raise RuntimeError(
            f"{key}: reverse conversion landed in unexpected state "
            f"type={fin_type} parent={fin_parent}. Halting."
        )


def main() -> None:
    user = os.environ.get("CUBRID_JIRA_USER")
    pw = os.environ.get("CUBRID_JIRA_PASSWORD")
    if not user or not pw:
        sys.exit("Set CUBRID_JIRA_USER and CUBRID_JIRA_PASSWORD")

    jar = CookieJar()
    opener = make_opener(jar)
    warm_session(opener)
    login_session(opener, user, pw)

    failures: list[str] = []
    for key in KEYS:
        try:
            reparent_one(opener, key, user, pw, NEW_PARENT)
            print(f"[{key}] ✓ reparented to {NEW_PARENT}\n")
        except Exception as e:
            print(f"[{key}] FAILED: {e}\n", file=sys.stderr)
            failures.append(key)
            break  # halt-on-first-error

    if failures:
        sys.exit(f"halted; failed keys: {failures}")
    print(f"all {len(KEYS)} reparented successfully.")


if __name__ == "__main__":
    main()
