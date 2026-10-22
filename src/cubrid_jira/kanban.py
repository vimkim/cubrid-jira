"""Personal, read-only JIRA view with all active tickets grouped by status."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from cubrid_jira.auth import resolve_credentials
from cubrid_jira.http import JiraError, exit_code_for_http, search_issues
from cubrid_jira.markdown import format_kanban_markdown
from cubrid_jira.server import DEFAULT_SERVER, normalize_server


MY_ACTIVE_JQL = (
    "assignee = currentUser() AND status NOT IN (Closed, Resolved) "
    "ORDER BY updated DESC, key ASC"
)
DISPLAY_FIELDS = "summary,status,issuetype,assignee,updated"
STATUS_ORDER = ("Open", "Reopened", "Confirmed", "Develop", "Test", "Handover")
STATUS_COLORS = {
    "Open": "yellow",
    "Reopened": "yellow",
    "Confirmed": "cyan",
    "Develop": "blue",
    "Test": "magenta",
    "Handover": "green",
}


def fetch_my_active_tickets(
    *, server: str = DEFAULT_SERVER, page_size: int = 100,
) -> list[dict]:
    """Fetch every page, advancing by the actual returned count if capped."""
    if page_size < 1:
        raise ValueError("page_size must be at least 1")

    issues: list[dict] = []
    seen: set[str] = set()
    start_at = 0
    expected_total = None
    while True:
        result = search_issues(
            MY_ACTIVE_JQL, fields=DISPLAY_FIELDS, max_results=page_size,
            start_at=start_at, server=server,
        )
        total = result["total"]
        if expected_total is None:
            expected_total = total
        if total != expected_total or result.get("startAt", start_at) != start_at:
            raise JiraError("JIRA search results changed while paging; run again.")
        batch = result["issues"]
        if not batch and start_at < total:
            raise JiraError("JIRA returned an empty page before all tickets were fetched.")
        for issue in batch:
            key = issue["key"]
            if key in seen:
                raise JiraError("JIRA returned duplicate tickets while paging; run again.")
            seen.add(key)
            issues.append(issue)
        start_at += len(batch)
        if start_at >= total:
            return issues


def group_by_status(issues: list[dict]) -> list[tuple[str, list[dict]]]:
    """Keep JIRA's newest-first order within each workflow-ordered group."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        status = (issue.get("fields", {}).get("status") or {}).get("name") or "Unknown"
        groups[status].append(issue)
    ranks = {name.casefold(): rank for rank, name in enumerate(STATUS_ORDER)}
    return sorted(
        groups.items(),
        key=lambda group: (ranks.get(group[0].casefold(), len(ranks)), group[0].casefold()),
    )


def print_kanban(
    groups: list[tuple[str, list[dict]]], *, console: Console,
    server: str = DEFAULT_SERVER,
) -> None:
    """Print wrapping terminal tables; server text is never console markup."""
    total = sum(len(issues) for _, issues in groups)
    console.print(Text(f"My active JIRA tickets ({total})", style="bold"))
    if not groups:
        console.print("No active tickets assigned to you.")
        return
    console.print(Text("  ·  ".join(f"{status}: {len(issues)}" for status, issues in groups)))
    base = normalize_server(server)
    show_type = console.width >= 100
    for status, issues in groups:
        console.print()
        table = Table(
            title=Text(f"{status} ({len(issues)})", style=STATUS_COLORS.get(status, "bold")),
            title_justify="left", box=box.SIMPLE_HEAD, expand=True,
            padding=(0, 1),
        )
        table.add_column("Key", no_wrap=True)
        if show_type:
            table.add_column("Type", max_width=24, overflow="fold")
        table.add_column("Updated", no_wrap=True)
        table.add_column("Summary", ratio=1, overflow="fold")
        for issue in issues:
            fields = issue.get("fields", {})
            key = issue["key"]
            issue_type = (fields.get("issuetype") or {}).get("name") or "?"
            updated = (fields.get("updated") or "")[:10]
            summary = " ".join((fields.get("summary") or "").split())
            row = [Text(key, style=f"link {base}/browse/{key}")]
            if show_type:
                row.append(Text(issue_type))
            table.add_row(*row, Text(updated), Text(summary))
        console.print(table)


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cubrid-jira-my-kanban",
        description="List all tickets assigned to you, grouped by status; exclude Closed and Resolved.",
    )
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"JIRA server (default: {DEFAULT_SERVER}).")
    parser.add_argument(
        "--page-size", type=_positive_int, default=100,
        help="Issues per request; every page is fetched (default: 100).",
    )
    parser.add_argument(
        "--output", choices=("text", "markdown", "json"), default="text",
        help="Terminal tables (default), Markdown tables, or grouped JSON.",
    )
    parser.add_argument("--no-color", action="store_true", help="Disable terminal colors.")
    args = parser.parse_args(argv)

    # currentUser() needs a signed-in account; fail before an anonymous query.
    resolve_credentials(args.server)
    try:
        issues = fetch_my_active_tickets(server=args.server, page_size=args.page_size)
    except JiraError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(exit_code_for_http(error.code))

    groups = group_by_status(issues)
    try:
        if args.output == "json":
            print(json.dumps({
                "total": len(issues),
                "groups": [
                    {"status": status, "count": len(tickets), "issues": tickets}
                    for status, tickets in groups
                ],
            }, ensure_ascii=False))
        elif args.output == "markdown":
            print(format_kanban_markdown(groups, server=args.server))
        else:
            print_kanban(
                groups, console=Console(file=sys.stdout, no_color=True if args.no_color else None),
                server=args.server,
            )
    except BrokenPipeError:
        # Commands such as `... | head` may close stdout early.
        sys.exit(0)


if __name__ == "__main__":
    main()
