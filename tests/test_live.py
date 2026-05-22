"""Live integration smoke test against http://jira.cubrid.org.

Skipped by default. Run with::

    uv run pytest -m live

Only the read path is exercised — no credentials, no writes, no CAPTCHA
risk. CBRD-1 is the oldest issue in the public CUBRID JIRA and is therefore
the safest stable target for a smoke test.
"""

from __future__ import annotations

import pytest

from cubrid_jira.http import fetch_issue


@pytest.mark.live
def test_fetch_issue_returns_real_data():
    data = fetch_issue("CBRD-1")
    assert data, "expected non-empty JSON for CBRD-1 from the live server"
    assert data.get("key") == "CBRD-1"
    fields = data.get("fields") or {}
    assert fields.get("summary"), "expected a summary field on CBRD-1"
