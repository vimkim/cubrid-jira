"""Pure unit tests for wizard.py — HTML parsing + form payload builders."""

from __future__ import annotations

import pytest

from cubrid_jira.wizard import (
    ISSUE_WIZARD,
    SUBTASK_WIZARD,
    build_issue_step1,
    build_issue_step3,
    build_issue_step4,
    build_subtask_step1,
    build_subtask_step3,
    build_subtask_step4,
    check_xsrf,
    parse_form,
    parse_issuetype_options,
    resolve_issuetype_id,
)


# --------------------------------------------------------------------------- #
# Endpoint constants
# --------------------------------------------------------------------------- #

def test_wizard_endpoint_constants_are_distinct():
    # The two wizards must NOT share endpoints — the forward wizard is
    # ConvertSubTask.jspa, the reverse is ConvertIssue.jspa.
    forward_paths = set(SUBTASK_WIZARD.values())
    reverse_paths = set(ISSUE_WIZARD.values())
    assert forward_paths.isdisjoint(reverse_paths)
    # Smoke-check each endpoint looks like a /secure/*.jspa path.
    for v in forward_paths | reverse_paths:
        assert v.startswith("/secure/")
        assert v.endswith(".jspa")


# --------------------------------------------------------------------------- #
# parse_form
# --------------------------------------------------------------------------- #

WIZARD_PAGE_HTML = """
<html>
  <form name="jiraform" action="/secure/ConvertSubTaskSetIssueType.jspa">
    <input type="hidden" name="atl_token" value="abc|123|lin"/>
    <input type="hidden" name="guid" value="GUID-XYZ"/>
    <input type="hidden" name="id" value="1418856"/>
    <select name="issuetype" id="issuetype">
      <option value="10500">Task</option>
      <option value="1">Bug</option>
      <option value="3">Story</option>
    </select>
  </form>
</html>
"""


def test_parse_form_extracts_token_and_guid():
    out = parse_form(WIZARD_PAGE_HTML)
    assert out["atl_token"] == "abc|123|lin"
    assert out["guid"] == "GUID-XYZ"


def test_parse_form_missing_returns_none():
    out = parse_form("<html>no fields here</html>")
    assert out == {"atl_token": None, "guid": None}


def test_parse_form_keeps_pipes_in_token():
    """The token has the shape <tab>|<token>|<source>; do NOT trim it."""
    html = '<input type="hidden" name="atl_token" value="tab|token|origin"/>'
    assert parse_form(html)["atl_token"] == "tab|token|origin"


# --------------------------------------------------------------------------- #
# parse_issuetype_options + resolve_issuetype_id
# --------------------------------------------------------------------------- #

def test_parse_issuetype_options():
    opts = parse_issuetype_options(WIZARD_PAGE_HTML)
    assert opts == [("10500", "Task"), ("1", "Bug"), ("3", "Story")]


def test_resolve_issuetype_id_case_insensitive():
    assert resolve_issuetype_id(WIZARD_PAGE_HTML, "Task") == "10500"
    assert resolve_issuetype_id(WIZARD_PAGE_HTML, "task") == "10500"
    assert resolve_issuetype_id(WIZARD_PAGE_HTML, "BUG") == "1"


def test_resolve_issuetype_id_unknown_raises_with_available_list():
    with pytest.raises(ValueError, match="No issuetype"):
        resolve_issuetype_id(WIZARD_PAGE_HTML, "Frobnicate")


def test_resolve_issuetype_id_handles_subtask_wizard():
    html = """
    <select name="issuetype">
      <option value="5">Sub-task</option>
    </select>
    """
    assert resolve_issuetype_id(html, "Sub-task") == "5"


def test_resolve_issuetype_id_no_select_raises():
    with pytest.raises(ValueError, match="locate <select"):
        resolve_issuetype_id("<html>nope</html>", "Task")


# --------------------------------------------------------------------------- #
# check_xsrf
# --------------------------------------------------------------------------- #

def test_check_xsrf_quiet_on_normal_html():
    check_xsrf("<html>conversion succeeded</html>")  # no raise


def test_check_xsrf_raises_on_jira_xsrf_page():
    with pytest.raises(RuntimeError, match="XSRF"):
        check_xsrf("<title>XSRF Security Token Missing</title>")


# --------------------------------------------------------------------------- #
# Payload builders — pure
# --------------------------------------------------------------------------- #

def test_build_subtask_step1_includes_issuetype_and_next():
    out = build_subtask_step1("1418856", "TOK", "GUID", "10500")
    assert out == {
        "id": "1418856",
        "guid": "GUID",
        "atl_token": "TOK",
        "issuetype": "10500",
        "Next >>": "Next >>",
    }


def test_build_subtask_step3_has_no_issuetype():
    out = build_subtask_step3("1418856", "TOK", "GUID")
    # Step 3 is pass-through Update Fields — issuetype was set in Step 1.
    assert "issuetype" not in out
    assert out["Next >>"] == "Next >>"


def test_build_subtask_step4_uses_finish_not_next():
    """Step 4 is the commit — must use 'Finish=Finish', not 'Next >>'."""
    out = build_subtask_step4("1", "T", "G")
    assert "Finish" in out
    assert "Next >>" not in out


def test_build_issue_step1_includes_parent_key():
    out = build_issue_step1("1", "T", "G", "5", "CBRD-26835")
    assert out["parentIssueKey"] == "CBRD-26835"
    assert out["issuetype"] == "5"
    assert out["Next >>"] == "Next >>"


def test_build_issue_step3_pass_through():
    out = build_issue_step3("1", "T", "G")
    # Reverse Step 3 must NOT carry parentIssueKey or issuetype again.
    assert "parentIssueKey" not in out
    assert "issuetype" not in out
    assert out["Next >>"] == "Next >>"


def test_build_issue_step4_uses_finish():
    out = build_issue_step4("1", "T", "G")
    assert out["Finish"] == "Finish"
    assert "Next >>" not in out
