"""Live integration smoke tests against http://jira.cubrid.org.

Skipped by default. Run with::

    uv run pytest -m live

The read smoke test below is safe and unconditional under the ``live``
marker — no credentials, no writes, no CAPTCHA risk. CBRD-1 is the oldest
issue in the public CUBRID JIRA and is therefore the safest stable
target.

The reparent round-trip is double-gated: it only runs if both
``-m live`` is set AND the ``CUBRID_JIRA_TEST_KEY`` env var points at a
sub-task the user is willing to bounce between two parents. Set
``CUBRID_JIRA_TEST_PARENT`` to the temporary parent it should move under;
the test moves it back to the original parent at the end.
"""

from __future__ import annotations

import os

import pytest

from cubrid_jira.cli import main
from cubrid_jira.http import fetch_issue


@pytest.mark.live
def test_fetch_issue_returns_real_data():
    data = fetch_issue("CBRD-1")
    assert data, "expected non-empty JSON for CBRD-1 from the live server"
    assert data.get("key") == "CBRD-1"
    fields = data.get("fields") or {}
    assert fields.get("summary"), "expected a summary field on CBRD-1"


@pytest.mark.live
def test_reparent_roundtrip_against_live_server(capsys):
    """Round-trip ``reparent`` against the real server, then move back.

    Requires:
      * ``CUBRID_JIRA_TEST_KEY``    — a sub-task you own and can move freely.
      * ``CUBRID_JIRA_TEST_PARENT`` — a temporary parent to move it under.
      * ``CUBRID_JIRA_USER`` + ``CUBRID_JIRA_PASSWORD`` (or ~/.netrc).
    """
    key = os.environ.get("CUBRID_JIRA_TEST_KEY")
    temp_parent = os.environ.get("CUBRID_JIRA_TEST_PARENT")
    if not key or not temp_parent:
        pytest.skip(
            "set CUBRID_JIRA_TEST_KEY + CUBRID_JIRA_TEST_PARENT to enable"
        )

    # Snapshot original parent so we can restore.
    before = fetch_issue(key)
    assert before, f"could not fetch {key} before reparent"
    original_parent = ((before.get("fields") or {}).get("parent") or {}).get("key")
    assert original_parent, (
        f"{key} has no parent — pick a real sub-task for CUBRID_JIRA_TEST_KEY"
    )
    assert original_parent != temp_parent, (
        f"CUBRID_JIRA_TEST_PARENT ({temp_parent}) must differ from "
        f"the current parent ({original_parent})"
    )

    # Forward: move to temp parent.
    main(["reparent", key, "--to", temp_parent, "--yes"])
    mid = fetch_issue(key)
    mid_parent = ((mid.get("fields") or {}).get("parent") or {}).get("key")
    mid_type = ((mid.get("fields") or {}).get("issuetype") or {}).get("name")
    assert mid_type == "Sub-task" and mid_parent == temp_parent

    # Restore: move back to original parent.
    main(["reparent", key, "--to", original_parent, "--yes"])
    after = fetch_issue(key)
    after_parent = ((after.get("fields") or {}).get("parent") or {}).get("key")
    after_type = ((after.get("fields") or {}).get("issuetype") or {}).get("name")
    assert after_type == "Sub-task" and after_parent == original_parent
