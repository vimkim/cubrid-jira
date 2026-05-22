#!/usr/bin/env python3
"""cubrid-jira-search: legacy alias for ``cubrid-jira search``.

Kept so the existing `/jira` skill and other consumers that call
``cubrid-jira-search CBRD-XXXXX`` continue to work unchanged. The argument
shape is identical to the old script.
"""

import argparse
import sys

from cubrid_jira_fetcher.cache import DEFAULT_DIR, resolve_cache_dir  # re-export
from cubrid_jira_fetcher.cli import _find_cached, cmd_search  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Search local cache for a CUBRID JIRA issue; fetch from web if missing. "
            "Equivalent to: cubrid-jira search ARGS"
        )
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


# Back-compat: callers used to import resolve_dir/find_cached from this module.
resolve_dir = resolve_cache_dir
find_cached = _find_cached


if __name__ == "__main__":
    sys.exit(main())
