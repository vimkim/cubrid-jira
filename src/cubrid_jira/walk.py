"""Recursive related-issue walking + on-disk caching.

This is the only module allowed to mix the HTTP and markdown layers — it
orchestrates fetching + rendering + saving.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cubrid_jira.http import fetch_issue, parse_issue_key
from cubrid_jira.markdown import extract_related_keys, format_issue_markdown


def issue_path(key: str, out_dir: Path, raw_json: bool) -> Path:
    ext = ".json" if raw_json else ".md"
    return out_dir / f"{key}{ext}"


def save_issue(data: dict, out_dir: Path, raw_json: bool = False) -> Path:
    """Write a single issue to ``out_dir/{KEY}.md`` (or ``.json``)."""
    key = data.get("key", "UNKNOWN")
    path = issue_path(key, out_dir, raw_json)
    content = (
        json.dumps(data, indent=2, ensure_ascii=False)
        if raw_json
        else format_issue_markdown(data)
    )
    path.write_text(content, encoding="utf-8")
    return path


def fetch_recursive(
    key: str,
    max_depth: int,
    visited: set[str],
    out_dir: Path,
    raw_json: bool = False,
    force: bool = False,
    current_depth: int = 0,
) -> None:
    if key in visited or current_depth > max_depth:
        return
    visited.add(key)

    path = issue_path(key, out_dir, raw_json)
    already_exists = path.exists()

    if not force and already_exists:
        print(f"Skipping {key} (already exists: {path})", file=sys.stderr)
        if current_depth < max_depth:
            data = fetch_issue(key)
            if data:
                for _rel, rkey in extract_related_keys(data):
                    fetch_recursive(
                        rkey, max_depth, visited, out_dir,
                        raw_json, force, current_depth + 1,
                    )
        return

    print(f"Fetching {key} (depth {current_depth})...", file=sys.stderr)
    data = fetch_issue(key)
    if not data:
        return

    save_issue(data, out_dir, raw_json=raw_json)
    print(f"  Saved -> {path}", file=sys.stderr)

    if current_depth < max_depth:
        for _rel, rkey in extract_related_keys(data):
            fetch_recursive(
                rkey, max_depth, visited, out_dir,
                raw_json, force, current_depth + 1,
            )


def bulk_fetch_main() -> None:
    """Entry point for the legacy ``cubrid-jira-fetch`` console script."""
    parser = argparse.ArgumentParser(
        description="Fetch CUBRID JIRA issue details and related issues.",
    )
    parser.add_argument(
        "issue",
        help="Issue key (e.g. CBRD-26463) or full browse URL",
    )
    parser.add_argument(
        "-d", "--output-dir",
        default="related_issues",
        metavar="DIR",
        help="Directory to save issue files (default: related_issues/)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        metavar="N",
        help="How many levels of related issues to follow (default: 1, 0 = no recursion)",
    )
    parser.add_argument(
        "--no-recurse",
        action="store_true",
        help="Only fetch the given issue, no related issues (same as --depth 0)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="raw_json",
        help="Save raw JSON instead of markdown",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download and overwrite already-saved issues",
    )
    args = parser.parse_args()

    try:
        key = parse_issue_key(args.issue)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    max_depth = 0 if args.no_recurse else args.depth
    visited: set[str] = set()
    fetch_recursive(
        key, max_depth, visited, out_dir,
        raw_json=args.raw_json, force=args.force,
    )
    print(
        f"\nDone. {len(visited)} issue(s) in {out_dir}/: {', '.join(sorted(visited))}"
    )
