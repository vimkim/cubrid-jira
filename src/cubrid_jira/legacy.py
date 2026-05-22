"""Deprecation shims for the legacy ``cubrid-jira-search`` and
``cubrid-jira-fetch`` console scripts.

These keep existing skills and shell aliases working while nudging users
toward the new unified ``cubrid-jira`` binary. Each entry point prints a
one-line stderr notice at startup, then delegates to the new code paths.
"""

from __future__ import annotations

import argparse
import sys

from cubrid_jira.cache import DEFAULT_DIR
from cubrid_jira.cli import cmd_search
from cubrid_jira.walk import bulk_fetch_main


def _warn(old: str, new: str) -> None:
    print(
        f"warning: `{old}` is deprecated and will be removed in a future release; "
        f"use `{new}` instead.",
        file=sys.stderr,
    )


def search_main() -> None:
    """Entry point for the legacy ``cubrid-jira-search`` console script."""
    _warn("cubrid-jira-search", "cubrid-jira search")
    parser = argparse.ArgumentParser(
        description=(
            "[deprecated] Search local cache for a CUBRID JIRA issue; fetch from web "
            "if missing. Equivalent to: cubrid-jira search ARGS"
        ),
    )
    parser.add_argument("issue", help="Issue key (e.g. CBRD-12345) or full browse URL")
    parser.add_argument(
        "-d", "--dir", default=None, metavar="DIR",
        help=f"Cache directory (default: $CUBRID_JIRA_DIR or {DEFAULT_DIR})",
    )
    parser.add_argument(
        "--no-recurse", action="store_true",
        help="When fetching, only fetch the given issue (no related issues)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch even if cached",
    )
    args = parser.parse_args()
    cmd_search(args)


def fetch_main() -> None:
    """Entry point for the legacy ``cubrid-jira-fetch`` console script."""
    _warn("cubrid-jira-fetch", "cubrid-jira search   (or write subcommands)")
    bulk_fetch_main()
