"""Pure helpers for the JIRA Server Convert wizard.

This module is the parsing + payload-building layer for the Convert wizard
flow that drives parent reparenting (see
``docs/reparent-subtasks-via-convert-wizard.md`` for the field report on
why the REST API alone is insufficient).

Layering rule
-------------
Pure functions only. This module must not import ``urllib`` — networking
lives in :mod:`cubrid_jira.session`.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Endpoint constants — distinct per wizard direction.
# --------------------------------------------------------------------------- #

SUBTASK_WIZARD = {
    # Sub-task -> Task / Bug / Story (clears parent).
    "page":  "/secure/ConvertSubTask.jspa",
    "step1": "/secure/ConvertSubTaskSetIssueType.jspa",
    "step3": "/secure/ConvertSubTaskUpdateFields.jspa",
    "step4": "/secure/ConvertSubTaskConvert.jspa",
}

ISSUE_WIZARD = {
    # Task / Bug / Story -> Sub-task with new parent.
    "page":  "/secure/ConvertIssue.jspa",
    "step1": "/secure/ConvertIssueSetIssueType.jspa",
    "step3": "/secure/ConvertIssueUpdateFields.jspa",
    "step4": "/secure/ConvertIssueConvert.jspa",
}


# --------------------------------------------------------------------------- #
# HTML parsing.
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r'name="atl_token"\s+value="([^"]+)"')
_GUID_RE = re.compile(r'name="guid"\s+value="([^"]+)"')
_ISSUETYPE_SELECT_RE = re.compile(
    r'<select[^>]*\bname="issuetype"[^>]*>(.*?)</select>',
    re.IGNORECASE | re.DOTALL,
)
_OPTION_RE = re.compile(
    r'<option[^>]*\bvalue="(\d+)"[^>]*>\s*([^<]+?)\s*</option>',
    re.IGNORECASE | re.DOTALL,
)


def parse_form(html: str) -> dict[str, str | None]:
    """Extract the hidden ``atl_token`` and ``guid`` fields from wizard HTML."""
    tok = _TOKEN_RE.search(html)
    guid = _GUID_RE.search(html)
    return {
        "atl_token": tok.group(1) if tok else None,
        "guid": guid.group(1) if guid else None,
    }


def parse_issuetype_options(html: str) -> list[tuple[str, str]]:
    """Return ``[(id, name), ...]`` for ``<select name="issuetype">`` options."""
    sel = _ISSUETYPE_SELECT_RE.search(html)
    if not sel:
        return []
    return [(v, n) for v, n in _OPTION_RE.findall(sel.group(1))]


def resolve_issuetype_id(html: str, wanted_name: str) -> str:
    """Look up a numeric issuetype id from the wizard's ``<select>``.

    Per-instance ids vary, so the prompt explicitly forbids hard-coding ``5``
    or ``10500`` — we resolve at runtime from the page the server just gave us.
    """
    options = parse_issuetype_options(html)
    if not options:
        raise ValueError(
            "Could not locate <select name='issuetype'> in wizard page — "
            "the response shape may have changed."
        )
    target = wanted_name.strip().lower()
    matches = [v for v, n in options if n.strip().lower() == target]
    if not matches:
        avail = ", ".join(repr(n) for _, n in options)
        raise ValueError(
            f"No issuetype named {wanted_name!r} on this wizard page. "
            f"Available: {avail}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous issuetype name {wanted_name!r}: matched {len(matches)} options"
        )
    return matches[0]


def check_xsrf(html: str) -> None:
    """JIRA returns HTTP 200 with this title body when XSRF gate trips."""
    if "XSRF Security Token Missing" in html:
        raise RuntimeError(
            "XSRF rejected — atl_token stale or X-Atlassian-Token header "
            "missing. The conversion did not happen."
        )


# --------------------------------------------------------------------------- #
# Payload builders — pure, no I/O.
# --------------------------------------------------------------------------- #

def build_subtask_step1(
    issue_id: str,
    atl_token: str,
    guid: str,
    issuetype_id: str,
) -> dict[str, str]:
    """Forward wizard Step 1: choose target issuetype (Sub-task -> Task)."""
    return {
        "id": issue_id,
        "guid": guid,
        "atl_token": atl_token,
        "issuetype": issuetype_id,
        "Next >>": "Next >>",
    }


def build_subtask_step3(issue_id: str, atl_token: str, guid: str) -> dict[str, str]:
    """Forward wizard Step 3: pass-through Update Fields."""
    return {
        "id": issue_id,
        "guid": guid,
        "atl_token": atl_token,
        "Next >>": "Next >>",
    }


def build_subtask_step4(issue_id: str, atl_token: str, guid: str) -> dict[str, str]:
    """Forward wizard Step 4: commit (Finish)."""
    return {
        "id": issue_id,
        "guid": guid,
        "atl_token": atl_token,
        "Finish": "Finish",
    }


def build_issue_step1(
    issue_id: str,
    atl_token: str,
    guid: str,
    issuetype_id: str,
    parent_key: str,
) -> dict[str, str]:
    """Reverse wizard Step 1: choose Sub-task issuetype + new parent key."""
    return {
        "id": issue_id,
        "guid": guid,
        "atl_token": atl_token,
        "issuetype": issuetype_id,
        "parentIssueKey": parent_key,
        "Next >>": "Next >>",
    }


def build_issue_step3(issue_id: str, atl_token: str, guid: str) -> dict[str, str]:
    """Reverse wizard Step 3: pass-through Update Fields."""
    return {
        "id": issue_id,
        "guid": guid,
        "atl_token": atl_token,
        "Next >>": "Next >>",
    }


def build_issue_step4(issue_id: str, atl_token: str, guid: str) -> dict[str, str]:
    """Reverse wizard Step 4: commit (Finish)."""
    return {
        "id": issue_id,
        "guid": guid,
        "atl_token": atl_token,
        "Finish": "Finish",
    }
