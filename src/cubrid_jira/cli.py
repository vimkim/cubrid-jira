"""cubrid-jira: parent CLI with read + write subcommands.

Subcommands
-----------
search      Cache-first read (existing behavior).
create      POST /rest/api/2/issue
comment     POST /rest/api/2/issue/{key}/comment
link        POST /rest/api/2/issueLink
transition  GET  /rest/api/2/issue/{key}/transitions  +  POST same path
assign      PUT  /rest/api/2/issue/{key}/assignee

All write subcommands accept ``--dry-run`` (default), ``--yes`` (required for
live writes), ``--server URL``, ``-d/--dir`` for the cache directory, and
``--output {text,json}`` to switch between human-readable status and a
single-line JSON result object for downstream pipelines.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cubrid_jira.auth import resolve_credentials
from cubrid_jira.cache import invalidate, resolve_cache_dir
from cubrid_jira.http import JiraClient, fetch_issue, parse_issue_key
from cubrid_jira.walk import fetch_recursive, save_issue

DEFAULT_SERVER = "http://jira.cubrid.org"
ALLOWED_LINK_TYPES = ("Blocks", "Cloners", "Duplicate", "Relates")


# --------------------------------------------------------------------------- #
# Pure payload builders — kept side-effect-free so tests can call them directly.
# --------------------------------------------------------------------------- #

def build_create_payload(
    project: str,
    issue_type: str,
    summary: str,
    *,
    description: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    components: list[str] | None = None,
) -> dict:
    fields: dict = {
        "project": {"key": project},
        "summary": summary,
        "issuetype": {"name": issue_type},
    }
    if priority:
        fields["priority"] = {"name": priority}
    if description is not None:
        fields["description"] = description
    if assignee:
        fields["assignee"] = {"name": assignee}
    if labels:
        fields["labels"] = list(labels)
    if components:
        fields["components"] = [{"name": c} for c in components]
    return {"fields": fields}


def build_link_payload(link_type: str, inward_key: str, outward_key: str) -> dict:
    return {
        "type": {"name": link_type},
        "inwardIssue": {"key": inward_key},
        "outwardIssue": {"key": outward_key},
    }


def build_comment_payload(body: str) -> dict:
    return {"body": body}


def build_transition_payload(transition_id: str) -> dict:
    return {"transition": {"id": transition_id}}


def build_assignee_payload(name: str | None) -> dict:
    return {"name": name if name else None}


def resolve_transition_id(transitions: list[dict], wanted_name: str) -> str:
    target = wanted_name.strip().lower()
    matches = [
        t for t in transitions
        if str(t.get("name", "")).strip().lower() == target
    ]
    if not matches:
        available = ", ".join(repr(t.get("name", "?")) for t in transitions) or "(none)"
        raise ValueError(
            f"No transition named {wanted_name!r}. Available: {available}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous transition name {wanted_name!r}: matched {len(matches)} transitions"
        )
    return str(matches[0]["id"])


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _is_dry_run(args) -> bool:
    """Dry-run is the default. --yes flips to live. --dry-run wins if both set."""
    if getattr(args, "dry_run", False):
        return True
    return not getattr(args, "yes", False)


def _output_format(args) -> str:
    return getattr(args, "output", "text")


def _make_client(args) -> JiraClient:
    user, pw = resolve_credentials(args.server)
    return JiraClient(
        args.server,
        user,
        pw,
        dry_run=_is_dry_run(args),
        output_format=_output_format(args),
    )


def _validate_link_type(link_type: str) -> None:
    if link_type not in ALLOWED_LINK_TYPES:
        allowed = " | ".join(ALLOWED_LINK_TYPES)
        print(
            f"Error: --type must be one of [{allowed}]; got {link_type!r}.",
            file=sys.stderr,
        )
        sys.exit(1)


def _emit(args, client: JiraClient, live_result: dict | None) -> None:
    """Centralised --output json emitter for write subcommands.

    In ``--output json`` mode this prints exactly one line on stdout:
    the dry-run plan or the live success result. In ``text`` mode it
    does nothing — the per-command handler is responsible for any
    stderr status lines.
    """
    if _output_format(args) != "json":
        return
    if _is_dry_run(args):
        payload = {"dry_run": True, "requests": client.recorded_requests}
    else:
        payload = live_result or {}
    print(json.dumps(payload, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# search (read-only)
# --------------------------------------------------------------------------- #

def _find_cached(key: str, directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob(f"{key}*.md"))


def cmd_search(args) -> None:
    try:
        key = parse_issue_key(args.issue)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    out_dir = resolve_cache_dir(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.force:
        cached = _find_cached(key, out_dir)
        if cached:
            print(f"# Found cached: {cached[0].name}", file=sys.stderr)
            print(cached[0].read_text(encoding="utf-8"))
            return

    print(f"# Fetching {key} from jira.cubrid.org ...", file=sys.stderr)
    max_depth = 0 if args.no_recurse else 1
    visited: set[str] = set()
    fetch_recursive(key, max_depth, visited, out_dir)

    cached = _find_cached(key, out_dir)
    if cached:
        print(cached[0].read_text(encoding="utf-8"))
    else:
        print(f"Error: Failed to fetch {key}", file=sys.stderr)
        sys.exit(1)


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #

def cmd_create(args) -> None:
    description = None
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8")

    payload = build_create_payload(
        project=args.project,
        issue_type=args.type,
        summary=args.summary,
        description=description,
        priority=args.priority,
        assignee=args.assignee,
        labels=args.labels,
        components=args.components,
    )

    client = _make_client(args)
    resp = client.request("POST", "/rest/api/2/issue", body=payload)
    new_key = (resp or {}).get("key")

    link_specs: list[tuple[str, str]] = []
    for k in args.link_relates or []:
        link_specs.append(("Relates", parse_issue_key(k)))
    for k in args.link_blocks or []:
        link_specs.append(("Blocks", parse_issue_key(k)))

    for link_type, other_key in link_specs:
        src = new_key or "<new-issue-key>"
        client.request(
            "POST",
            "/rest/api/2/issueLink",
            body=build_link_payload(link_type, src, other_key),
        )

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    live_result: dict | None = None
    if new_key:
        cache_dir = resolve_cache_dir(args.dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        full = fetch_issue(new_key)
        if full:
            save_issue(full, cache_dir)
        url = f"{args.server.rstrip('/')}/browse/{new_key}"
        live_result = {"key": new_key, "url": url}
        if _output_format(args) == "text":
            print(f"Created {new_key}: {url}", file=sys.stderr)
    else:
        if _output_format(args) == "text":
            print(
                "Warning: create response did not include a 'key' field; "
                "cache not updated.",
                file=sys.stderr,
            )
    _emit(args, client, live_result)


# --------------------------------------------------------------------------- #
# comment
# --------------------------------------------------------------------------- #

def cmd_comment(args) -> None:
    key = parse_issue_key(args.issue)
    body_text = Path(args.body_file).read_text(encoding="utf-8")

    client = _make_client(args)
    resp = client.request(
        "POST",
        f"/rest/api/2/issue/{key}/comment",
        body=build_comment_payload(body_text),
    )

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    live_result = {"issue": key, "comment_id": (resp or {}).get("id")}
    if _output_format(args) == "text":
        print(f"Commented on {key}; cache entry invalidated.", file=sys.stderr)
    _emit(args, client, live_result)


# --------------------------------------------------------------------------- #
# link
# --------------------------------------------------------------------------- #

def cmd_link(args) -> None:
    _validate_link_type(args.link_type)
    src = parse_issue_key(args.issue)
    dst = parse_issue_key(args.to)

    client = _make_client(args)
    client.request(
        "POST",
        "/rest/api/2/issueLink",
        body=build_link_payload(args.link_type, src, dst),
    )

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(src, cache_dir)
    invalidate(dst, cache_dir)
    if _output_format(args) == "text":
        print(
            f"Linked {src} -[{args.link_type}]-> {dst}; cache invalidated for both.",
            file=sys.stderr,
        )
    _emit(args, client, {"inward": src, "outward": dst, "type": args.link_type})


# --------------------------------------------------------------------------- #
# transition
# --------------------------------------------------------------------------- #

def cmd_transition(args) -> None:
    key = parse_issue_key(args.issue)
    client = _make_client(args)

    resp = client.request("GET", f"/rest/api/2/issue/{key}/transitions")
    transitions = (resp or {}).get("transitions") or []

    if not args.to:
        if _output_format(args) == "json":
            print(json.dumps(
                {"issue": key, "transitions": transitions}, ensure_ascii=False
            ))
        else:
            print(f"Available transitions for {key}:", file=sys.stderr)
            for t in transitions:
                print(f"  {t.get('id')}: {t.get('name')}")
        return

    try:
        tid = resolve_transition_id(transitions, args.to)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    client.request(
        "POST",
        f"/rest/api/2/issue/{key}/transitions",
        body=build_transition_payload(tid),
    )

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    if _output_format(args) == "text":
        print(
            f"Transitioned {key} -> {args.to}; cache entry invalidated.",
            file=sys.stderr,
        )
    _emit(args, client, {"issue": key, "transition_id": tid, "to": args.to})


# --------------------------------------------------------------------------- #
# assign
# --------------------------------------------------------------------------- #

def cmd_assign(args) -> None:
    key = parse_issue_key(args.issue)
    payload = build_assignee_payload(args.to)

    client = _make_client(args)
    client.request("PUT", f"/rest/api/2/issue/{key}/assignee", body=payload)

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    assignee = args.to if args.to else None
    if _output_format(args) == "text":
        action = "unassigned" if assignee is None else f"assigned to {assignee}"
        print(f"{key} {action}; cache entry invalidated.", file=sys.stderr)
    _emit(args, client, {"issue": key, "assignee": assignee})


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #

def _add_write_globals(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved request without sending it (default behavior).",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Required for live writes; without it commands stay in dry-run mode.",
    )
    p.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f"JIRA server base URL (default: {DEFAULT_SERVER}).",
    )
    p.add_argument(
        "-d", "--dir",
        default=None,
        metavar="DIR",
        help="Cache directory (default: $CUBRID_JIRA_DIR or "
             "~/.local/share/cubrid-jira/issues/).",
    )
    p.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Output format. 'json' prints a single-line result object on stdout "
             "(for piping into jq/agents); errors still go to stderr.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cubrid-jira",
        description=(
            "Read + write CUBRID JIRA issues. Write commands are dry-run by "
            "default; pass --yes to actually send."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    p_search = sub.add_parser(
        "search",
        help="Cache-first read of one issue; prints markdown to stdout.",
    )
    p_search.add_argument("issue", help="Issue key (e.g. CBRD-12345) or full browse URL")
    p_search.add_argument(
        "-d", "--dir", default=None, metavar="DIR",
        help="Cache directory (default: $CUBRID_JIRA_DIR or "
             "~/.local/share/cubrid-jira/issues/).",
    )
    p_search.add_argument("--no-recurse", action="store_true",
                          help="On cache miss, only fetch the requested issue.")
    p_search.add_argument("--force", action="store_true",
                          help="Bypass cache and re-fetch.")
    p_search.set_defaults(func=cmd_search)

    p_create = sub.add_parser("create", help="Create a new issue.")
    p_create.add_argument("--project", required=True, help="Project key, e.g. CBRD.")
    p_create.add_argument("--type", required=True, help="Issue type, e.g. Bug, Task.")
    p_create.add_argument("--summary", required=True, help="Issue summary (title).")
    p_create.add_argument("--description-file", metavar="PATH",
                          help="File whose contents become the issue description.")
    p_create.add_argument("--priority",
                          help="One of: Blocker, Critical, Major, Minor, Trivial.")
    p_create.add_argument("--assignee", help="JIRA username to assign on creation.")
    p_create.add_argument("--label", action="append", dest="labels", metavar="LABEL",
                          help="Repeat for multiple labels.")
    p_create.add_argument("--component", action="append", dest="components",
                          metavar="NAME", help="Repeat for multiple components.")
    p_create.add_argument("--link-relates", action="append", dest="link_relates",
                          metavar="KEY",
                          help="After creation, link the new issue as 'Relates' to KEY.")
    p_create.add_argument("--link-blocks", action="append", dest="link_blocks",
                          metavar="KEY",
                          help="After creation, link the new issue as 'Blocks' to KEY.")
    _add_write_globals(p_create)
    p_create.set_defaults(func=cmd_create)

    p_comment = sub.add_parser("comment", help="Add a comment to an issue.")
    p_comment.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_comment.add_argument("--body-file", required=True, metavar="PATH",
                           help="File whose contents become the comment body.")
    _add_write_globals(p_comment)
    p_comment.set_defaults(func=cmd_comment)

    p_link = sub.add_parser("link", help="Create a link between two issues.")
    p_link.add_argument("issue", help="Source issue key (inwardIssue).")
    p_link.add_argument("--type", required=True, dest="link_type",
                        help="Link type: Blocks | Cloners | Duplicate | Relates.")
    p_link.add_argument("--to", required=True,
                        help="Target issue key (outwardIssue).")
    _add_write_globals(p_link)
    p_link.set_defaults(func=cmd_link)

    p_transition = sub.add_parser(
        "transition",
        help="Transition an issue to another workflow state.",
    )
    p_transition.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_transition.add_argument(
        "--to", default=None,
        help="Target transition name (case-insensitive). Omit to list available transitions.",
    )
    _add_write_globals(p_transition)
    p_transition.set_defaults(func=cmd_transition)

    p_assign = sub.add_parser("assign", help="Set or clear an issue's assignee.")
    p_assign.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_assign.add_argument("--to", required=True,
                          help='JIRA username, or "" to unassign.')
    _add_write_globals(p_assign)
    p_assign.set_defaults(func=cmd_assign)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
