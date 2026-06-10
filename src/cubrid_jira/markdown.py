"""Jira-wiki → Markdown rendering for fetched issue JSON.

Layering rule
-------------
This module must not import ``urllib``. Networking lives in
:mod:`cubrid_jira.http`; this module is pure rendering and parsing.

It does shell out to ``pandoc`` for the wiki-to-markdown conversion; if
pandoc is missing the body falls through as plain text rather than failing
the whole command.
"""

from __future__ import annotations

import subprocess

from cubrid_jira.http import JIRA_BASE  # constant-only import — no cycles


def jira_to_markdown(text: str) -> str:
    """Convert Jira wiki markup to markdown via pandoc. Plain-text fallback."""
    try:
        result = subprocess.run(
            ["pandoc", "-f", "jira", "-t", "markdown", "--wrap=none"],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return text


def extract_related_keys(data: dict) -> list[tuple[str, str]]:
    """Return list of (relationship, key) tuples for every related issue."""
    related: list[tuple[str, str]] = []
    fields = data.get("fields", {})

    parent = fields.get("parent")
    if parent:
        related.append(("parent", parent["key"]))

    for sub in fields.get("subtasks", []):
        related.append(("subtask", sub["key"]))

    for link in fields.get("issuelinks", []):
        link_type = link["type"]["name"]
        if "inwardIssue" in link:
            related.append((f"{link_type} (inward)", link["inwardIssue"]["key"]))
        if "outwardIssue" in link:
            related.append((f"{link_type} (outward)", link["outwardIssue"]["key"]))

    return related


def _md_cell(value: object) -> str:
    """Make a server-controlled value safe inside a markdown table cell.

    Escapes ``|`` and flattens newlines so a stray pipe or multi-line field
    can't break the table layout.
    """
    return (
        str(value)
        .replace("|", "\\|")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def format_search_results_markdown(result: dict) -> str:
    """Render a ``/rest/api/2/search`` response as a compact markdown table.

    Pure rendering, like :func:`format_issue_markdown` — no network import.
    One row per issue: key (linked) · status · type · assignee · updated ·
    summary. Every server-controlled cell is escaped (see :func:`_md_cell`)
    so pipes or newlines can't corrupt the table.
    """
    issues = result.get("issues", [])
    total = result.get("total", len(issues))
    if not issues:
        return f"# JQL search — 0 of {total} matching issues"

    lines = [
        f"# JQL search — {len(issues)} of {total} matching issues",
        "",
        "| Key | Status | Type | Assignee | Updated | Summary |",
        "|---|---|---|---|---|---|",
    ]
    for issue in issues:
        key = issue.get("key", "?")
        fields = issue.get("fields", {})
        status = _md_cell((fields.get("status") or {}).get("name", "?"))
        issue_type = _md_cell((fields.get("issuetype") or {}).get("name", "?"))
        assignee = _md_cell((fields.get("assignee") or {}).get("displayName", "Unassigned"))
        updated = _md_cell((fields.get("updated") or "")[:10])
        summary = _md_cell(fields.get("summary") or "")
        link = f"{JIRA_BASE}/browse/{key}"
        lines.append(
            f"| [{key}]({link}) | {status} | {issue_type} | "
            f"{assignee} | {updated} | {summary} |"
        )
    return "\n".join(lines)


def format_issue_markdown(data: dict) -> str:
    """Format an issue dict as a human-readable markdown document."""
    if not data:
        return "(no data)"

    key = data.get("key", "?")
    fields = data.get("fields", {})

    lines: list[str] = []
    summary = fields.get("summary", "(no summary)")
    lines.append(f"# [{key}] {summary}")
    lines.append(f"\n<{JIRA_BASE}/browse/{key}>")

    lines.append("\n## Metadata\n")
    lines.append("| Field | Value |")
    lines.append("|---|---|")

    status = fields.get("status", {}).get("name", "?")
    lines.append(f"| Status | {status} |")

    priority = fields.get("priority", {}).get("name", "?")
    lines.append(f"| Priority | {priority} |")

    issue_type = fields.get("issuetype", {}).get("name", "?")
    lines.append(f"| Type | {issue_type} |")

    assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
    lines.append(f"| Assignee | {assignee} |")

    reporter = (fields.get("reporter") or {}).get("displayName", "?")
    lines.append(f"| Reporter | {reporter} |")

    resolution = (fields.get("resolution") or {}).get("name", "Unresolved")
    lines.append(f"| Resolution | {resolution} |")

    components = [c["name"] for c in fields.get("components", [])]
    if components:
        lines.append(f"| Components | {', '.join(components)} |")

    fix_versions = [v["name"] for v in fields.get("fixVersions", [])]
    if fix_versions:
        lines.append(f"| Fix Version | {', '.join(fix_versions)} |")

    target_versions = [v["name"] for v in fields.get("customfield_210441", []) or []]
    if target_versions:
        lines.append(f"| Target Version | {', '.join(target_versions)} |")

    created = (fields.get("created") or "")[:10]
    updated = (fields.get("updated") or "")[:10]
    lines.append(f"| Created | {created} |")
    lines.append(f"| Updated | {updated} |")

    desc = fields.get("description") or ""
    if desc:
        lines.append("\n## Description\n")
        lines.append(jira_to_markdown(desc))

    comments = fields.get("comment", {}).get("comments", [])
    if comments:
        lines.append(f"\n## Comments ({len(comments)} total)\n")
        for c in comments:
            author = (c.get("author") or {}).get("displayName", "?")
            date = (c.get("created") or "")[:10]
            body = jira_to_markdown(c.get("body") or "")
            lines.append(f"### {author} — {date}\n")
            lines.append(body)
            lines.append("")

    related = extract_related_keys(data)
    if related:
        lines.append("\n## Related Issues\n")
        for rel, rkey in related:
            rlink = f"{JIRA_BASE}/browse/{rkey}"
            lines.append(f"- **{rel}**: [{rkey}]({rlink})")

    return "\n".join(lines)
