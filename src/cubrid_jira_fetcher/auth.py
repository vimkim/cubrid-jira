"""Credential resolution for CUBRID JIRA writes.

Resolution order (first hit wins):
    1. CUBRID_JIRA_USER + CUBRID_JIRA_PASSWORD env vars
    2. ~/.netrc entry for the JIRA host
    3. Exit 1 with an instructive message (no interactive prompt)
"""

from __future__ import annotations

import netrc
import os
import sys
from urllib.parse import urlparse


def _host_of(server: str) -> str:
    host = urlparse(server).hostname
    return host or "jira.cubrid.org"


def resolve_credentials(server: str = "http://jira.cubrid.org") -> tuple[str, str]:
    user = os.environ.get("CUBRID_JIRA_USER")
    pw = os.environ.get("CUBRID_JIRA_PASSWORD")
    if user and pw:
        return user, pw

    host = _host_of(server)
    try:
        nrc = netrc.netrc()
    except (FileNotFoundError, netrc.NetrcParseError):
        nrc = None

    if nrc is not None:
        auth = nrc.authenticators(host)
        if auth:
            n_user, _, n_pw = auth
            if n_user and n_pw:
                return n_user, n_pw

    print(
        "Error: No CUBRID JIRA credentials found.\n"
        "  Set environment variables:\n"
        "    export CUBRID_JIRA_USER=<username>\n"
        "    export CUBRID_JIRA_PASSWORD=<password>\n"
        f"  OR add a ~/.netrc entry for {host}:\n"
        f"    machine {host}\n"
        "      login <username>\n"
        "      password <password>\n"
        "  (chmod 600 ~/.netrc)",
        file=sys.stderr,
    )
    sys.exit(1)


def mask_password(_: str) -> str:
    return "***"
