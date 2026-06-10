"""cubrid-jira: parent CLI with read + write subcommands.

Subcommands
-----------
search      Cache-first read of ONE issue by key (existing behavior).
jql         GET  /rest/api/2/search — list issues matching a JQL query.
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
from cubrid_jira.cache import invalidate, resolve_cache_dir, resolve_field_map_path
from cubrid_jira.fields import (
    AmbiguousFieldError,
    FieldSpecError,
    build_name_index,
    decode_field_value,
    is_custom_field_id,
    load_field_index,
    parse_field_spec,
    resolve_name,
    save_field_index,
)
from cubrid_jira.http import (
    JiraClient,
    JiraError,
    fetch_issue,
    parse_issue_key,
    search_issues,
)
from cubrid_jira.markdown import format_search_results_markdown
from cubrid_jira.session import SessionClient
from cubrid_jira.walk import fetch_recursive, save_issue
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
    resolve_issuetype_id,
)

DEFAULT_SERVER = "http://jira.cubrid.org"
ALLOWED_LINK_TYPES = ("Blocks", "Cloners", "Duplicate", "Relates")
DRY_RUN_TOKEN_PLACEHOLDER = "<extracted-at-runtime>"
DRY_RUN_ISSUETYPE_PLACEHOLDER = "<resolved-at-runtime>"

# Columns the `jql` text table renders; the request always includes these in
# text mode so a narrowed --fields can't blank out the table.
JQL_DISPLAY_FIELDS = ("summary", "status", "issuetype", "assignee", "updated")
JQL_DEFAULT_FIELDS = ",".join(JQL_DISPLAY_FIELDS)


def _non_negative_int(value: str) -> int:
    """argparse type: reject negative ints (e.g. --max, --start-at)."""
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value!r}")
    return ivalue


def _exit_code_for_http(code: int | None) -> int:
    """Map an HTTP status onto the project exit-code contract.

    0 ok | 1 generic | 2 401 | 3 403 | 4 404 | 5 400. ``None`` (transport
    error, no HTTP status) and any unlisted status fall through to 1.
    """
    return {400: 5, 401: 2, 403: 3, 404: 4}.get(code or 0, 1)


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
    custom_fields: dict[str, object] | None = None,
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
    if custom_fields:
        for k, v in custom_fields.items():
            fields[k] = v
    return {"fields": fields}


def build_update_payload(
    *,
    summary: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    components: list[str] | None = None,
    custom_fields: dict[str, object] | None = None,
) -> dict:
    # labels/components use `is not None` so callers can deliberately pass []
    # to *clear* the field — Jira treats absent keys and empty lists differently.
    fields: dict = {}
    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        fields["description"] = description
    if priority is not None:
        fields["priority"] = {"name": priority}
    if labels is not None:
        fields["labels"] = list(labels)
    if components is not None:
        fields["components"] = [{"name": c} for c in components]
    if custom_fields:
        for k, v in custom_fields.items():
            fields[k] = v
    return {"fields": fields}


def build_link_payload(link_type: str, inward_key: str, outward_key: str) -> dict:
    return {
        "type": {"name": link_type},
        "inwardIssue": {"key": inward_key},
        "outwardIssue": {"key": outward_key},
    }


def build_comment_payload(body: str) -> dict:
    return {"body": body}


def build_comment_update_payload(body: str) -> dict:
    # Identical to build_comment_payload today. Kept separate so future
    # divergence (e.g. an edit-only `visibility` field) doesn't require
    # renaming callers.
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


def _make_session(args) -> SessionClient:
    user, pw = resolve_credentials(args.server)
    return SessionClient(
        args.server,
        user,
        pw,
        dry_run=_is_dry_run(args),
        output_format=_output_format(args),
    )


def _resolve_custom_fields(args, client: JiraClient) -> dict[str, object]:
    """Translate ``--field FIELD=VALUE`` repetitions into a ``customfield_NNN``
    payload subdict.

    Resolution order per FIELD:
      1. raw ``customfield_\\d+`` id → used verbatim, no network.
      2. display name → resolved against the on-disk name->id cache.
      3. cache miss → one ``GET /rest/api/2/field`` (executes even in
         dry-run, since GET is a read) refreshes the cache, then re-resolves.

    Errors are raised as ``FieldSpecError`` / ``AmbiguousFieldError`` —
    the caller is responsible for translating those to a CLI exit.
    """
    specs: list[str] = getattr(args, "fields", None) or []
    if not specs:
        return {}

    # Decode VALUE per spec: a leading `{` or `[` JSON-parses (so select /
    # cascading-select fields can be passed as {"value": "..."}); anything
    # else stays a raw string.
    parsed: list[tuple[str, object]] = []
    for s in specs:
        name, raw_value = parse_field_spec(s)
        parsed.append((name, decode_field_value(raw_value)))

    # Fast path: every spec is a raw customfield id; skip the network entirely.
    if all(is_custom_field_id(n) for n, _ in parsed):
        return {n: v for n, v in parsed}

    map_path = resolve_field_map_path(getattr(args, "dir", None))
    index = load_field_index(map_path)

    needed = [n for n, _ in parsed if not is_custom_field_id(n)]
    if any(n not in index for n in needed):
        listing = client.request("GET", "/rest/api/2/field")
        # Jira Server returns a bare JSON array; tolerate {"fields": [...]} too.
        if isinstance(listing, dict):
            listing = listing.get("fields", []) or []
        if not isinstance(listing, list):
            listing = []
        index = build_name_index(listing)
        save_field_index(map_path, index)

    out: dict[str, object] = {}
    for name, value in parsed:
        if is_custom_field_id(name):
            out[name] = value
            continue
        fid = resolve_name(name, index)
        if fid is None:
            raise FieldSpecError(
                f"unknown JIRA field name {name!r}. "
                "Open <server>/rest/api/2/field to list available fields, "
                "or pass the explicit id (e.g. --field customfield_12345=...)."
            )
        out[fid] = value
    return out


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
# jql (read-only, unauthenticated list search)
# --------------------------------------------------------------------------- #

def cmd_jql(args) -> None:
    output = _output_format(args)
    # JSON returns exactly the requested fields. The text table needs its
    # display columns, so union them in — a narrowed --fields can't blank the
    # table, but any extra fields the user asked for are still fetched.
    if output == "json":
        fields = args.fields
    else:
        requested = [f.strip() for f in args.fields.split(",") if f.strip()]
        fields = ",".join(dict.fromkeys([*JQL_DISPLAY_FIELDS, *requested]))

    try:
        result = search_issues(
            args.jql,
            fields=fields,
            max_results=args.max,
            start_at=args.start_at,
        )
    except JiraError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(_exit_code_for_http(e.code))

    if output == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(format_search_results_markdown(result))


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #

def cmd_create(args) -> None:
    description = None
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8")

    client = _make_client(args)
    try:
        custom = _resolve_custom_fields(args, client)
    except (FieldSpecError, AmbiguousFieldError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    payload = build_create_payload(
        project=args.project,
        issue_type=args.type,
        summary=args.summary,
        description=description,
        priority=args.priority,
        assignee=args.assignee,
        labels=args.labels,
        components=args.components,
        custom_fields=custom,
    )

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
        live_result = {
            "key": new_key,
            "id": (resp or {}).get("id"),
            "self": (resp or {}).get("self"),
            "url": url,
        }
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
# comment-list / comment-update / comment-delete
# --------------------------------------------------------------------------- #

def _format_comment_line(c: dict) -> str:
    cid = c.get("id", "?")
    author = (c.get("author") or {}).get("displayName") or "(unknown)"
    created = c.get("created", "?")
    body = (c.get("body") or "").replace("\n", " ")
    if len(body) > 80:
        body = body[:80] + "..."
    return f"{cid} | {author} | {created} | {body}"


def cmd_comment_list(args) -> None:
    key = parse_issue_key(args.issue)
    client = _make_client(args)
    resp = client.request("GET", f"/rest/api/2/issue/{key}/comment") or {}
    comments = resp.get("comments") or []
    total = resp.get("total", len(comments))

    limit = args.limit
    if limit is not None and limit >= 0 and len(comments) > limit:
        # Jira returns oldest-first; "most recent" means the tail.
        comments = comments[-limit:]

    if _output_format(args) == "json":
        out = {
            "issue": key,
            "total": total,
            "comments": [
                {
                    "id": c.get("id"),
                    "author": (c.get("author") or {}).get("displayName"),
                    "created": c.get("created"),
                    "body": c.get("body", ""),
                }
                for c in comments
            ],
        }
        print(json.dumps(out, ensure_ascii=False))
        return

    for c in comments:
        print(_format_comment_line(c))


def cmd_comment_update(args) -> None:
    key = parse_issue_key(args.issue)
    if args.body_file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = Path(args.body_file).read_text(encoding="utf-8")

    client = _make_client(args)
    client.request(
        "PUT",
        f"/rest/api/2/issue/{key}/comment/{args.id}",
        body=build_comment_update_payload(body_text),
    )

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    live_result = {"issue": key, "comment_id": args.id, "updated": True}
    if _output_format(args) == "text":
        print(
            f"Updated comment {args.id} on {key}; cache entry invalidated.",
            file=sys.stderr,
        )
    _emit(args, client, live_result)


def cmd_comment_delete(args) -> None:
    key = parse_issue_key(args.issue)

    if not _is_dry_run(args):
        # Irreversible — print a one-line informational warning before sending.
        print(
            f"# About to DELETE comment {args.id} on {key}.",
            file=sys.stderr,
        )

    client = _make_client(args)
    client.request("DELETE", f"/rest/api/2/issue/{key}/comment/{args.id}")

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    live_result = {"issue": key, "comment_id": args.id, "deleted": True}
    if _output_format(args) == "text":
        print(
            f"Deleted comment {args.id} on {key}; cache entry invalidated.",
            file=sys.stderr,
        )
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
# update
# --------------------------------------------------------------------------- #

def cmd_update(args) -> None:
    key = parse_issue_key(args.issue)

    description = None
    if args.description_file:
        if args.description_file == "-":
            description = sys.stdin.read()
        else:
            description = Path(args.description_file).read_text(encoding="utf-8")

    client = _make_client(args)
    try:
        custom = _resolve_custom_fields(args, client)
    except (FieldSpecError, AmbiguousFieldError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    payload = build_update_payload(
        summary=args.summary,
        description=description,
        priority=args.priority,
        labels=args.labels,
        components=args.components,
        custom_fields=custom,
    )
    if not payload["fields"]:
        print(
            "Error: nothing to update; pass at least one of "
            "--description-file / --summary / --priority / --label / "
            "--component / --field",
            file=sys.stderr,
        )
        sys.exit(1)

    client.request("PUT", f"/rest/api/2/issue/{key}", body=payload)

    updated_fields = sorted(payload["fields"].keys())

    if _is_dry_run(args):
        _emit(args, client, None)
        return

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    if _output_format(args) == "text":
        print(
            f"Updated {key} fields {updated_fields}; cache entry invalidated.",
            file=sys.stderr,
        )
    _emit(args, client, {"issue": key, "updated_fields": updated_fields})


# --------------------------------------------------------------------------- #
# convert-to-issue / convert-to-subtask / reparent
# --------------------------------------------------------------------------- #
#
# All three subcommands drive the JIRA Server Convert wizard, because
# PUT /rest/api/2/issue/{KEY} silently no-ops on the parent field for this
# server's Field Configuration Scheme. Full rationale + traps:
#   docs/reparent-subtasks-via-convert-wizard.md

def _refresh_form(
    html: str,
    fallback_token: str,
    fallback_guid: str,
) -> tuple[str, str]:
    """Re-extract ``atl_token``/``guid`` from a wizard step's response HTML.

    Stale tokens give the same XSRF rejection as missing ones, so we always
    re-parse. In dry-run mode ``html`` is empty and we keep the placeholders.
    """
    form = parse_form(html)
    return (
        form["atl_token"] or fallback_token,
        form["guid"] or fallback_guid,
    )


def _drive_subtask_to_task(
    session: SessionClient,
    issue_id: str,
    atl_token: str,
    guid: str,
    issuetype_id: str,
) -> None:
    """Run wizard steps 1, 3, 4 of ConvertSubTask (Step 2 skipped for Task)."""
    html = session.html_post(
        SUBTASK_WIZARD["step1"],
        build_subtask_step1(issue_id, atl_token, guid, issuetype_id),
    )
    check_xsrf(html)
    atl_token, guid = _refresh_form(html, atl_token, guid)

    html = session.html_post(
        SUBTASK_WIZARD["step3"],
        build_subtask_step3(issue_id, atl_token, guid),
    )
    check_xsrf(html)
    atl_token, guid = _refresh_form(html, atl_token, guid)

    html = session.html_post(
        SUBTASK_WIZARD["step4"],
        build_subtask_step4(issue_id, atl_token, guid),
    )
    check_xsrf(html)


def _drive_task_to_subtask(
    session: SessionClient,
    issue_id: str,
    atl_token: str,
    guid: str,
    issuetype_id: str,
    parent_key: str,
) -> None:
    """Run wizard steps 1, 3, 4 of ConvertIssue (Step 2 skipped)."""
    html = session.html_post(
        ISSUE_WIZARD["step1"],
        build_issue_step1(issue_id, atl_token, guid, issuetype_id, parent_key),
    )
    check_xsrf(html)
    atl_token, guid = _refresh_form(html, atl_token, guid)

    html = session.html_post(
        ISSUE_WIZARD["step3"],
        build_issue_step3(issue_id, atl_token, guid),
    )
    check_xsrf(html)
    atl_token, guid = _refresh_form(html, atl_token, guid)

    html = session.html_post(
        ISSUE_WIZARD["step4"],
        build_issue_step4(issue_id, atl_token, guid),
    )
    check_xsrf(html)


def _fetch_meta(key: str) -> tuple[str, str, str | None]:
    """Return ``(issue_id, issuetype_name, parent_key_or_None)`` for ``key``.

    On a fetch failure the helper exits 4 — ``fetch_issue`` already printed
    the underlying HTTP/network reason to stderr.
    """
    data = fetch_issue(key)
    if not data:
        sys.exit(4)
    issue_id = str(data.get("id", ""))
    fields = data.get("fields") or {}
    cur_type = (fields.get("issuetype") or {}).get("name") or ""
    cur_parent = (fields.get("parent") or {}).get("key")
    return issue_id, cur_type, cur_parent


def _require_atl(form: dict[str, str | None], where: str) -> tuple[str, str]:
    if not form.get("atl_token"):
        print(
            f"Error: could not extract atl_token from {where}. Has the wizard "
            "page shape changed?",
            file=sys.stderr,
        )
        sys.exit(1)
    return form["atl_token"], (form.get("guid") or "")


def cmd_convert_to_issue(args) -> None:
    key = parse_issue_key(args.issue)
    target_type = args.type or "Task"

    issue_id, cur_type, cur_parent = _fetch_meta(key)
    if cur_type != "Sub-task":
        print(
            f"Error: {key} is currently type={cur_type!r}; convert-to-issue "
            "requires a Sub-task. (Use 'reparent' to move between parents.)",
            file=sys.stderr,
        )
        sys.exit(1)

    session = _make_session(args)

    if _is_dry_run(args):
        _drive_subtask_to_task(
            session,
            issue_id,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_ISSUETYPE_PLACEHOLDER,
        )
        _emit(args, session, None)
        return

    session.login()
    page = session.html_get(f"{SUBTASK_WIZARD['page']}?id={issue_id}")
    atl_token, guid = _require_atl(parse_form(page), "ConvertSubTask page")
    try:
        issuetype_id = resolve_issuetype_id(page, target_type)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    _drive_subtask_to_task(session, issue_id, atl_token, guid, issuetype_id)

    _, after_type, after_parent = _fetch_meta(key)
    if after_type != target_type or after_parent is not None:
        print(
            f"!!! WARNING: {key} did NOT land in the expected state.\n"
            f"!!!   wanted type={target_type!r}, parent=(none)\n"
            f"!!!   got    type={after_type!r}, parent={after_parent!r}\n"
            f"!!! Manual recovery may be required: "
            f"http://jira.cubrid.org/browse/{key}",
            file=sys.stderr,
        )
        sys.exit(1)

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    if cur_parent:
        invalidate(cur_parent, cache_dir)

    if _output_format(args) == "text":
        prev = cur_parent or "(none)"
        print(
            f"Converted {key}: Sub-task -> {target_type} "
            f"(previous parent: {prev}); cache invalidated.",
            file=sys.stderr,
        )
    _emit(args, session, {
        "issue": key,
        "type": target_type,
        "previous_parent": cur_parent,
    })


def cmd_convert_to_subtask(args) -> None:
    if not (args.to or "").strip():
        print("Error: --to <PARENT> is required and must be non-empty.",
              file=sys.stderr)
        sys.exit(1)

    key = parse_issue_key(args.issue)
    parent_key = parse_issue_key(args.to)
    target_type = args.type or "Sub-task"

    issue_id, cur_type, _cur_parent = _fetch_meta(key)
    if cur_type == "Sub-task":
        print(
            f"Error: {key} is already a Sub-task. Use 'reparent' to move "
            "it under a different parent.",
            file=sys.stderr,
        )
        sys.exit(1)

    _parent_id, parent_type, _ = _fetch_meta(parent_key)
    if parent_type == "Sub-task":
        print(
            f"Error: --to {parent_key!r} is itself a Sub-task. Choose a "
            "non-subtask parent.",
            file=sys.stderr,
        )
        sys.exit(1)

    session = _make_session(args)

    if _is_dry_run(args):
        _drive_task_to_subtask(
            session,
            issue_id,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_ISSUETYPE_PLACEHOLDER,
            parent_key,
        )
        _emit(args, session, None)
        return

    session.login()
    page = session.html_get(f"{ISSUE_WIZARD['page']}?id={issue_id}")
    atl_token, guid = _require_atl(parse_form(page), "ConvertIssue page")
    try:
        issuetype_id = resolve_issuetype_id(page, target_type)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    _drive_task_to_subtask(
        session, issue_id, atl_token, guid, issuetype_id, parent_key
    )

    _, after_type, after_parent = _fetch_meta(key)
    if after_type != target_type or after_parent != parent_key:
        print(
            f"!!! WARNING: {key} did NOT land in the expected state.\n"
            f"!!!   wanted type={target_type!r}, parent={parent_key!r}\n"
            f"!!!   got    type={after_type!r}, parent={after_parent!r}\n"
            f"!!! Manual recovery may be required: "
            f"http://jira.cubrid.org/browse/{key}",
            file=sys.stderr,
        )
        sys.exit(1)

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    invalidate(parent_key, cache_dir)

    if _output_format(args) == "text":
        print(
            f"Converted {key} to {target_type} under {parent_key}; "
            "cache invalidated for both.",
            file=sys.stderr,
        )
    _emit(args, session, {
        "issue": key,
        "parent": parent_key,
        "type": target_type,
    })


def cmd_reparent(args) -> None:
    if not (args.to or "").strip():
        print("Error: --to <PARENT> is required and must be non-empty.",
              file=sys.stderr)
        sys.exit(1)

    key = parse_issue_key(args.issue)
    new_parent_key = parse_issue_key(args.to)
    intermediate_type = "Task"
    target_type = "Sub-task"

    issue_id, cur_type, cur_parent = _fetch_meta(key)
    if cur_type != "Sub-task":
        print(
            f"Error: {key} is currently type={cur_type!r}; reparent requires "
            "a Sub-task. (Use 'convert-to-subtask' to attach a non-subtask.)",
            file=sys.stderr,
        )
        sys.exit(1)
    if cur_parent == new_parent_key:
        print(
            f"Error: {key} is already a Sub-task of {new_parent_key} — nothing to do.",
            file=sys.stderr,
        )
        sys.exit(1)

    _np_id, np_type, _ = _fetch_meta(new_parent_key)
    if np_type == "Sub-task":
        print(
            f"Error: --to {new_parent_key!r} is itself a Sub-task. Choose a "
            "non-subtask parent.",
            file=sys.stderr,
        )
        sys.exit(1)

    session = _make_session(args)

    if _is_dry_run(args):
        # Plan both halves with placeholders.
        _drive_subtask_to_task(
            session,
            issue_id,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_ISSUETYPE_PLACEHOLDER,
        )
        _drive_task_to_subtask(
            session,
            issue_id,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_TOKEN_PLACEHOLDER,
            DRY_RUN_ISSUETYPE_PLACEHOLDER,
            new_parent_key,
        )
        _emit(args, session, None)
        return

    session.login()

    # --- Forward half (Sub-task -> Task; drops parent). --------------- #
    fwd_page = session.html_get(f"{SUBTASK_WIZARD['page']}?id={issue_id}")
    atl_token, guid = _require_atl(
        parse_form(fwd_page), "ConvertSubTask page"
    )
    try:
        fwd_type = resolve_issuetype_id(fwd_page, intermediate_type)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    _drive_subtask_to_task(session, issue_id, atl_token, guid, fwd_type)

    _, inter_type, inter_parent = _fetch_meta(key)
    if inter_type != intermediate_type or inter_parent is not None:
        print(
            f"!!! WARNING: forward conversion of {key} did NOT land in "
            f"the expected intermediate state.\n"
            f"!!!   wanted type={intermediate_type!r}, parent=(none)\n"
            f"!!!   got    type={inter_type!r}, parent={inter_parent!r}\n"
            f"!!! Reverse step not attempted. Halting.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- ATOMICITY BOUNDARY ------------------------------------------- #
    # From here on, any failure leaves the issue as a Task with no parent.
    try:
        rev_page = session.html_get(f"{ISSUE_WIZARD['page']}?id={issue_id}")
        rev_form = parse_form(rev_page)
        if not rev_form["atl_token"]:
            raise RuntimeError(
                "could not extract atl_token from ConvertIssue page"
            )
        rev_type = resolve_issuetype_id(rev_page, target_type)
        _drive_task_to_subtask(
            session,
            issue_id,
            rev_form["atl_token"],
            rev_form.get("guid") or "",
            rev_type,
            new_parent_key,
        )
        _, final_type, final_parent = _fetch_meta(key)
        if final_type != target_type or final_parent != new_parent_key:
            raise RuntimeError(
                f"final state after reverse conversion: "
                f"type={final_type!r}, parent={final_parent!r}"
            )
    except Exception as exc:
        print(
            "\n!!! ATOMICITY WARNING: reparent FAILED after the forward "
            "conversion succeeded.\n"
            f"!!! {key} is now a Task with no parent and needs recovery.\n"
            f"!!! Open http://jira.cubrid.org/browse/{key} and either:\n"
            f"!!!   - Convert to Sub-task of {new_parent_key} via the web UI, or\n"
            f"!!!   - Re-run: cubrid-jira convert-to-subtask {key} "
            f"--to {new_parent_key} --yes\n"
            f"!!! Cause: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    cache_dir = resolve_cache_dir(args.dir)
    invalidate(key, cache_dir)
    if cur_parent:
        invalidate(cur_parent, cache_dir)
    invalidate(new_parent_key, cache_dir)

    if _output_format(args) == "text":
        old = cur_parent or "(none)"
        print(
            f"Reparented {key}: {old} -> {new_parent_key}; cache invalidated "
            "for all three keys.",
            file=sys.stderr,
        )
    _emit(args, session, {
        "issue": key,
        "from_parent": cur_parent,
        "to_parent": new_parent_key,
    })


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #

def _add_field_flag(p: argparse.ArgumentParser) -> None:
    """Add the repeatable ``--field FIELD=VALUE`` flag.

    FIELD may be a raw ``customfield_NNN`` id or a display name (e.g.
    ``"QA Scenario"``). Names are resolved against the cached field map
    populated by a one-time ``GET /rest/api/2/field`` and refreshed on
    cache miss. Repeat ``--field`` to set multiple fields:

      cubrid-jira create ... --field "QA Scenario=N/A" \
                             --field customfield_210566=...
    """
    p.add_argument(
        "--field",
        action="append",
        dest="fields",
        default=None,
        metavar="FIELD=VALUE",
        help=(
            'Set an arbitrary JIRA field. FIELD is a custom-field id '
            '(e.g. customfield_210565) or display name (e.g. "QA Scenario"); '
            'names are resolved via /rest/api/2/field and cached on disk. '
            'Repeat for multiple fields. Example: '
            '--field "QA Scenario=Not applicable; analysis ticket".'
        ),
    )


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

    p_jql = sub.add_parser(
        "jql",
        help="Run a JQL query and list matching issues (read-only, unauthenticated).",
    )
    p_jql.add_argument(
        "jql",
        metavar="JQL",
        help='JQL query string, e.g. "assignee = jdoe AND status not in '
             '(Resolved, Closed, Done) ORDER BY updated DESC".',
    )
    p_jql.add_argument(
        "--fields", default=JQL_DEFAULT_FIELDS,
        help="Comma-separated issue fields to request (default: "
             f"{JQL_DEFAULT_FIELDS}). Honored as-is for --output json; for the "
             "text table the display columns are always included.",
    )
    p_jql.add_argument(
        "--max", type=_non_negative_int, default=50, metavar="N",
        help="Maximum issues to return (maxResults; default 50).",
    )
    p_jql.add_argument(
        "--start-at", type=_non_negative_int, default=0, metavar="N",
        help="0-based index of the first result (startAt; for pagination).",
    )
    p_jql.add_argument(
        "--output", choices=("text", "json"), default="text",
        help="Output format. 'text' prints a markdown table; 'json' prints the "
             "raw /rest/api/2/search response on one line (for jq/agents).",
    )
    p_jql.set_defaults(func=cmd_jql)

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
    _add_field_flag(p_create)
    _add_write_globals(p_create)
    p_create.set_defaults(func=cmd_create)

    p_comment = sub.add_parser("comment", help="Add a comment to an issue.")
    p_comment.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_comment.add_argument("--body-file", required=True, metavar="PATH",
                           help="File whose contents become the comment body.")
    _add_write_globals(p_comment)
    p_comment.set_defaults(func=cmd_comment)

    p_comment_list = sub.add_parser(
        "comment-list",
        help="List comments on an issue (read-only; always hits the network).",
    )
    p_comment_list.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_comment_list.add_argument(
        "--limit", type=int, default=50, metavar="N",
        help="Keep only the N most recent comments (default 50). "
             "Pass 0 for no limit.",
    )
    _add_write_globals(p_comment_list)
    p_comment_list.set_defaults(func=cmd_comment_list)

    p_comment_update = sub.add_parser(
        "comment-update",
        help="Edit an existing comment on an issue.",
    )
    p_comment_update.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_comment_update.add_argument(
        "--id", required=True, metavar="COMMENT-ID",
        help="Comment ID to update (capture via `comment-list --output json`).",
    )
    p_comment_update.add_argument(
        "--body-file", required=True, metavar="PATH",
        help="File whose contents become the new comment body; '-' reads stdin.",
    )
    _add_write_globals(p_comment_update)
    p_comment_update.set_defaults(func=cmd_comment_update)

    p_comment_delete = sub.add_parser(
        "comment-delete",
        help="Delete a comment on an issue (irreversible).",
    )
    p_comment_delete.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_comment_delete.add_argument(
        "--id", required=True, metavar="COMMENT-ID",
        help="Comment ID to delete (capture via `comment-list --output json`).",
    )
    _add_write_globals(p_comment_delete)
    p_comment_delete.set_defaults(func=cmd_comment_delete)

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

    p_update = sub.add_parser(
        "update",
        help="Edit fields on an existing issue (summary, description, priority, "
             "labels, components).",
    )
    p_update.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_update.add_argument("--summary", default=None,
                          help="New issue summary (title).")
    p_update.add_argument("--description-file", metavar="PATH", default=None,
                          help="File whose contents replace the issue description. "
                               "Use '-' to read from stdin.")
    p_update.add_argument("--priority", default=None,
                          help="One of: Blocker, Critical, Major, Minor, Trivial.")
    p_update.add_argument("--label", action="append", dest="labels", metavar="LABEL",
                          default=None,
                          help="Repeat for multiple labels. NOTE: replaces the "
                               "full label list (Jira REST 'fields' semantics).")
    p_update.add_argument("--component", action="append", dest="components",
                          metavar="NAME", default=None,
                          help="Repeat for multiple components. NOTE: replaces the "
                               "full component list.")
    _add_field_flag(p_update)
    _add_write_globals(p_update)
    p_update.set_defaults(func=cmd_update)

    p_convert_issue = sub.add_parser(
        "convert-to-issue",
        help="Convert a Sub-task to a top-level issue (drops the parent).",
    )
    p_convert_issue.add_argument("issue", help="Sub-task key, e.g. CBRD-12345.")
    p_convert_issue.add_argument(
        "--type", default="Task",
        help="Target issue type (default: Task). Resolved against the wizard "
             "page's <select name='issuetype'> at runtime; numeric IDs are NOT "
             "hard-coded since they vary per JIRA install.",
    )
    _add_write_globals(p_convert_issue)
    p_convert_issue.set_defaults(func=cmd_convert_to_issue)

    p_convert_sub = sub.add_parser(
        "convert-to-subtask",
        help="Convert a top-level issue to a Sub-task under --to.",
    )
    p_convert_sub.add_argument("issue", help="Issue key, e.g. CBRD-12345.")
    p_convert_sub.add_argument("--to", required=True,
                               help="Parent issue key (must NOT be a Sub-task).")
    p_convert_sub.add_argument(
        "--type", default="Sub-task",
        help="Target sub-task type (default: Sub-task). Resolved at runtime.",
    )
    _add_write_globals(p_convert_sub)
    p_convert_sub.set_defaults(func=cmd_convert_to_subtask)

    p_reparent = sub.add_parser(
        "reparent",
        help="Move a Sub-task from its current parent to --to (composes the "
             "two convert-* subcommands).",
    )
    p_reparent.add_argument("issue", help="Sub-task key, e.g. CBRD-12345.")
    p_reparent.add_argument("--to", required=True,
                            help="New parent issue key (must NOT be a Sub-task).")
    _add_write_globals(p_reparent)
    p_reparent.set_defaults(func=cmd_reparent)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
