"""Jira-wiki <-> Markdown rendering for Jira issue text.

Layering rule
-------------
This module must not import ``urllib``. Networking lives in
:mod:`cubrid_jira.http`; this module is pure rendering and parsing.

It does shell out to ``pandoc`` for the wiki-to-markdown conversion; if
pandoc is missing the body falls through as plain text rather than failing
the whole read command. Markdown-to-Jira conversion is used for writes and
fails hard because silently uploading raw Markdown is a user-visible mistake.
"""

from __future__ import annotations

import re
import subprocess
import sys

from cubrid_jira.http import JIRA_BASE  # constant-only import — no cycles
from cubrid_jira.spacing import normalize_korean_jira_spacing

KOREAN = r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]"
MARKDOWN_INLINE_RE = re.compile(
    r"""
    (?<!\*)\*\*([^\n]*?[^\s*])\*\*(?!\*)      |   # **bold**
    (?<!\*)\*([^\s*\n][^\n]*?[^\s*])\*(?!\*) |   # *italic*
    `([^\s`\n][^\n]*?[^\s`])`                    # `code`
    """,
    re.VERBOSE,
)
MARKDOWN_FENCE_RE = re.compile(r"^\s*(```|~~~)")
MARKDOWN_FENCE_OPEN_RE = re.compile(
    r"^[ \t]*(?P<marker>`{3,}|~{3,})[^\r\n]*(?:\r?\n)?$"
)
SIMPLE_TABLE_SEPARATOR_RE = re.compile(
    r"^[ \t]*-{2,}(?:[ \t]+-{2,})+[ \t]*$"
)
SIMPLE_TABLE_HEADER_RE = re.compile(r"\S[ \t]{2,}\S")
JIRA_VERBATIM_RE = re.compile(r"^\s*\{(?:code|noformat)(?::[^}]*)?\}\s*$")
JIRA_CODE_LANGUAGE_RE = re.compile(
    r"^(?P<indent>\s*)\{code:(?P<language>[^}|]+)"
    r"(?P<options>\|[^}]*)?\}(?P<trailing>\s*)$"
)

# Source-code formatter names exposed by jira.cubrid.org. Pandoc copies any
# Markdown fence label into {code:<label>}, but Jira renders a visible error
# when that label is not in this vocabulary.
JIRA_CODE_LANGUAGES = frozenset(
    {
        "actionscript", "ada", "applescript", "bash", "c", "c#", "c++",
        "cpp", "css", "erlang", "go", "groovy", "haskell", "html", "java",
        "javascript", "js", "json", "lua", "none", "nyan", "objc", "perl",
        "php", "python", "r", "rainbow", "ruby", "scala", "sh", "sql",
        "swift", "visualbasic", "xml", "yaml",
    }
)
JIRA_CODE_LANGUAGE_ALIASES = {
    "cc": "cpp",
    "console": "none",
    "csharp": "c#",
    "cxx": "cpp",
    "objective-c": "objc",
    "plaintext": "none",
    "shell": "sh",
    "shell-session": "none",
    "text": "none",
    "txt": "none",
    "yml": "yaml",
}
PANDOC_ROUND_TRIP_MARKDOWN_FORMAT = (
    "markdown-simple_tables-multiline_tables-grid_tables+pipe_tables"
)


class MarkdownConversionError(RuntimeError):
    """Markdown could not be converted to Jira wiki markup."""


_pandoc_read_warned = False


def _warn_pandoc_read_once(detail: str) -> None:
    """Explain the raw-markup fallback on stderr, at most once per process."""
    global _pandoc_read_warned
    if _pandoc_read_warned:
        return
    _pandoc_read_warned = True
    suffix = f" ({detail})" if detail else ""
    print(
        f"Warning: pandoc cannot convert Jira wiki markup{suffix}; showing it raw. "
        "The jira reader needs pandoc >= 2.9.1 — the RHEL 8 package is 2.0.6.",
        file=sys.stderr,
    )


def jira_to_markdown(text: str) -> str:
    """Convert Jira wiki markup to markdown via pandoc. Raw-markup fallback.

    Falls back to the input whenever pandoc cannot do the conversion: no binary,
    a timeout, or a pandoc built without the ``jira`` reader. That last case exits
    non-zero with empty stdout, so returning stdout unconditionally would render
    every issue as though it had no description — a silent, plausible-looking
    wrong answer rather than a visible failure.
    """
    try:
        result = subprocess.run(
            [
                "pandoc", "-f", "jira",
                "-t", PANDOC_ROUND_TRIP_MARKDOWN_FORMAT,
                "--wrap=none",
            ],
            input=text,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return text

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        _warn_pandoc_read_once(stderr.splitlines()[0] if stderr else "")
        return text

    return result.stdout.strip()


def _is_korean_char(ch: str) -> bool:
    return re.match(KOREAN, ch) is not None


def _normalize_markdown_span(span: str) -> str:
    if span.startswith("**") and span.endswith("**"):
        return f"**{span[2:-2].strip()}**"
    if span.startswith("*") and span.endswith("*"):
        return f"*{span[1:-1].strip()}*"
    if span.startswith("`") and span.endswith("`"):
        return f"`{span[1:-1].strip()}`"
    return span


def _fix_markdown_inline_spacing(text: str) -> str:
    result: list[str] = []
    last = 0

    for match in MARKDOWN_INLINE_RE.finditer(text):
        start, end = match.span()
        span = _normalize_markdown_span(match.group(0))

        result.append(text[last:start])

        prev_char = text[start - 1] if start > 0 else ""
        next_char = text[end] if end < len(text) else ""

        if prev_char and _is_korean_char(prev_char):
            if not result[-1].endswith(" "):
                result.append(" ")

        result.append(span)

        if next_char and _is_korean_char(next_char):
            result.append(" ")

        last = end

    result.append(text[last:])
    return "".join(result)


def normalize_korean_markdown_spacing(text: str) -> str:
    """Insert spaces between Korean text and Markdown inline spans.

    Fenced code blocks are left unchanged.
    """
    result: list[str] = []
    fence_marker = ""

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content):]
        fence_match = MARKDOWN_FENCE_RE.match(content)

        if fence_match:
            marker = fence_match.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = ""
            result.append(line)
            continue

        if fence_marker:
            result.append(line)
        else:
            result.append(_fix_markdown_inline_spacing(content) + newline)

    return "".join(result)


def sanitize_markdown(text: str) -> str:
    """Ensure blank lines before headings, lists, and fenced code blocks."""
    lines = text.split("\n")
    out: list[str] = []
    fence_marker = ""
    for i, line in enumerate(lines):
        fence_match = MARKDOWN_FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence_marker == marker:
                fence_marker = ""
                out.append(line)
                continue
            if not fence_marker:
                fence_marker = marker

        if fence_marker and not fence_match:
            out.append(line)
            continue

        if i > 0 and out and out[-1].strip() != "":
            needs_blank = False
            if re.match(r"^#{1,6}\s", line):
                needs_blank = True
            elif re.match(r"^[-*+]\s", line):
                needs_blank = not re.match(r"^[-*+]\s", out[-1])
            elif re.match(r"^\d+\.\s", line):
                needs_blank = not re.match(r"^\d+\.\s", out[-1])
            elif fence_match:
                needs_blank = True
            if needs_blank:
                out.append("")
        out.append(line)
    return "\n".join(out)


def _find_simple_table_separator(text: str) -> int | None:
    """Return the 1-based line of an unsafe simple-table ruler, if any."""
    lines = text.splitlines()
    previous_line = ""
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        opener = MARKDOWN_FENCE_OPEN_RE.match(line)
        if opener:
            marker = opener.group("marker")
            closing_re = re.compile(
                rf"^[ \t]*{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$"
            )
            line_index += 1
            while line_index < len(lines):
                if closing_re.match(lines[line_index]):
                    line_index += 1
                    break
                line_index += 1
            previous_line = ""
            continue

        if (
            SIMPLE_TABLE_SEPARATOR_RE.match(line)
            and SIMPLE_TABLE_HEADER_RE.search(previous_line)
        ):
            return line_index + 1

        previous_line = line
        line_index += 1

    return None


def _protect_escaped_pipes(text: str) -> tuple[str, str]:
    """Hide Markdown-escaped pipes from Pandoc's Jira table writer."""
    token = "CUBRIDJIRAESCAPEDPIPE"
    while token in text:
        token += "X"

    result: list[str] = []
    fence_marker = ""

    for line in text.splitlines(keepends=True):
        fence_match = MARKDOWN_FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not fence_marker:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = ""
            result.append(line)
            continue

        if fence_marker:
            result.append(line)
            continue

        protected_line: list[str] = []
        for char in line:
            if char == "|":
                preceding_backslashes = 0
                for previous in reversed(protected_line):
                    if previous != "\\":
                        break
                    preceding_backslashes += 1
                if preceding_backslashes % 2:
                    protected_line.pop()
                    protected_line.append(token)
                    continue
            protected_line.append(char)
        result.extend(protected_line)

    return "".join(result), token


def _protect_fenced_code_contents(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Replace closed fenced-code payloads with tokens Pandoc cannot alter."""
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    replacements: list[tuple[str, str]] = []
    line_index = 0

    while line_index < len(lines):
        opener = MARKDOWN_FENCE_OPEN_RE.match(lines[line_index])
        if not opener:
            result.append(lines[line_index])
            line_index += 1
            continue

        marker = opener.group("marker")
        closing_re = re.compile(
            rf"^[ \t]*{re.escape(marker[0])}{{{len(marker)},}}[ \t]*(?:\r?\n)?$"
        )
        closing_index = line_index + 1
        while closing_index < len(lines):
            if closing_re.match(lines[closing_index]):
                break
            closing_index += 1

        if closing_index == len(lines):
            result.extend(lines[line_index:])
            break

        token = f"CUBRIDJIRACODEBLOCK{len(replacements)}"
        while token in text:
            token += "X"
        opener_newline = "\r\n" if lines[line_index].endswith("\r\n") else "\n"
        content = "".join(lines[line_index + 1:closing_index])

        result.append(lines[line_index])
        result.append(token + opener_newline)
        result.append(lines[closing_index])
        replacements.append((token, content))
        line_index = closing_index + 1

    return "".join(result), replacements


def _restore_fenced_code_contents(
    jira_text: str, replacements: list[tuple[str, str]]
) -> str:
    for token, content in replacements:
        placeholder_line = token + "\n"
        if placeholder_line not in jira_text:
            raise MarkdownConversionError(
                "pandoc did not preserve a fenced code block placeholder"
            )
        jira_text = jira_text.replace(placeholder_line, content, 1)
    return jira_text


def _fix_jira_lines_outside_verbatim_blocks(text: str, fix_line) -> str:
    result: list[str] = []
    in_verbatim = False

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content):]

        if JIRA_VERBATIM_RE.match(content):
            in_verbatim = not in_verbatim
            result.append(line)
            continue

        if in_verbatim:
            result.append(line)
        else:
            result.append(fix_line(content) + newline)

    return "".join(result)


def fix_jira_bold_code_nesting(text: str) -> str:
    """Split Jira ``{{monospace}}`` spans out of Jira ``*bold*`` spans."""

    def _fix_line(line: str) -> str:
        if re.match(r"\s*\*+\s", line):
            return line

        def _split_bold(match):
            inner = match.group(1)
            if "{{" not in inner:
                return match.group(0)

            segments = re.split(r"(\{\{.*?\}\})", inner)
            parts: list[str] = []
            for segment in segments:
                if segment.startswith("{{") and segment.endswith("}}"):
                    parts.append(segment)
                else:
                    stripped = segment.strip()
                    if stripped:
                        parts.append(f"*{stripped}*")
            return " ".join(parts)

        return re.sub(r"\*(?!\s)([^*\n]+?)(?<!\s)\*", _split_bold, line)

    return _fix_jira_lines_outside_verbatim_blocks(text, _fix_line)


def normalize_jira_code_languages(text: str) -> str:
    """Prevent unsupported Jira source-code formatter labels.

    Known aliases are canonicalized. Any other label outside this Jira
    instance's supported vocabulary becomes ``none``, preserving the verbatim
    block without exposing a renderer error in the published issue.
    """
    result: list[str] = []

    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content):]
        match = JIRA_CODE_LANGUAGE_RE.match(content)
        if not match:
            result.append(line)
            continue

        language = match.group("language").strip().lower()
        language = JIRA_CODE_LANGUAGE_ALIASES.get(language, language)
        if language not in JIRA_CODE_LANGUAGES:
            language = "none"

        options = match.group("options") or ""
        result.append(
            f'{match.group("indent")}{{code:{language}{options}}}'
            f'{match.group("trailing")}{newline}'
        )

    return "".join(result)


def md_to_jira(md_text: str) -> str:
    """Convert Markdown to Jira wiki markup via pandoc."""
    try:
        result = subprocess.run(
            ["pandoc", "--from", "markdown", "--to", "jira"],
            input=md_text,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout
    except FileNotFoundError as e:
        raise MarkdownConversionError("pandoc is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise MarkdownConversionError("pandoc timed out") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip()
        message = "pandoc failed"
        if detail:
            message = f"{message}: {detail}"
        raise MarkdownConversionError(message) from e


def markdown_to_jira_body(md_text: str) -> str:
    """Render local Markdown as Jira wiki markup suitable for writes."""
    simple_table_line = _find_simple_table_separator(md_text)
    if simple_table_line is not None:
        raise MarkdownConversionError(
            "unsafe Pandoc simple table at line "
            f"{simple_table_line}; convert it to a pipe table before uploading"
        )

    protected_markdown, code_replacements = _protect_fenced_code_contents(md_text)
    protected_markdown, escaped_pipe_token = _protect_escaped_pipes(
        protected_markdown
    )
    spaced_markdown = normalize_korean_markdown_spacing(protected_markdown)
    jira_text = md_to_jira(sanitize_markdown(spaced_markdown))
    jira_text = jira_text.replace(escaped_pipe_token, "&#124;")
    jira_text = normalize_jira_code_languages(jira_text)
    jira_text = fix_jira_bold_code_nesting(jira_text)
    jira_text = normalize_korean_jira_spacing(jira_text)
    return _restore_fenced_code_contents(jira_text, code_replacements)


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
