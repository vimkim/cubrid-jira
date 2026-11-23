"""Refresh tests/data/jira_render_snapshot.json from Jira's real renderer.

    uv run python tests/render_snapshot.py                  # render only what is missing
    uv run python tests/render_snapshot.py --all            # render everything again
    uv run python tests/render_snapshot.py --adopt-pandoc   # record this machine's pandoc

The snapshot maps each markup string the oracle needs to the HTML that
jira.cubrid.org renders for it, and records the pandoc version that wrote the
Markdown cases' keys. Offline tests read the snapshot; this script is the only
code that needs the network. Rerunning it renders nothing new. Entries nothing
needs any more are dropped.

With a different pandoc than the recorded one, with none recorded (a snapshot
whose ``"pandoc"`` is null), or with none that reads Jira, the Markdown cases
are skipped: nothing is dropped and the recorded version stays.
``--adopt-pandoc`` records the local pandoc instead and drops the Markdown
entries it does not need.

The render API answers anonymously. Credentials are sent only when
``resolve_credentials_optional()`` finds some. A 401 stops the run at once:
Jira Server locks the account after a few failed basic-auth attempts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import markup_cases
from conftest import PANDOC_VERSION
from cubrid_jira.http import JIRA_BASE, basic_auth_header, resolve_credentials_optional

SNAPSHOT_PATH = Path(__file__).parent / "data" / "jira_render_snapshot.json"
RENDER_URL = f"{JIRA_BASE}/rest/api/1.0/render"
BATCH_SIZE = 50
SENTINEL = "ZQSENTINEL"
_SENTINEL_PARAGRAPH = re.compile(rf"<p>\s*{SENTINEL}(\d{{4}})\s*</p>")


class RenderError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Snapshot:
    """Rendered HTML per markup string, plus the pandoc that wrote the Markdown keys."""

    pandoc: str | None
    renders: dict[str, str]


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Snapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Snapshot(None, {})
    return Snapshot(data["pandoc"], data["renders"])


def write_snapshot(snapshot: Snapshot, path: Path = SNAPSHOT_PATH) -> None:
    """Write sorted JSON through a temp file so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"pandoc": snapshot.pandoc, "renders": snapshot.renders}
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def markdown_skip_reason(local: str | None, recorded: str | None) -> str | None:
    """Why the Markdown cases cannot use the snapshot here, or None when they can.

    Their keys are what pandoc wrote, and another version may write other text.
    A snapshot that records no version cannot vouch for any, so it skips too.
    """
    if local is None:
        return "pandoc lacks Jira formats"
    if recorded is None:
        return (
            f"the snapshot records no pandoc version, this machine has pandoc {local}"
        )
    if recorded != local:
        return (
            f"the snapshot was recorded with pandoc {recorded}, "
            f"this machine has pandoc {local}"
        )
    return None


def render_one(markup: str) -> str:
    """Render one markup string with the wiki renderer; never retries."""
    headers = {"Content-Type": "application/json", "X-Atlassian-Token": "no-check"}
    creds = resolve_credentials_optional()
    if creds is not None:
        headers["Authorization"] = basic_auth_header(*creds)
    body = json.dumps(
        {"rendererType": "atlassian-wiki-renderer", "unrenderedMarkup": markup}
    ).encode("utf-8")
    request = urllib.request.Request(RENDER_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8").strip()
    except urllib.error.HTTPError as error:
        raise RenderError(
            f"render API answered HTTP {error.code}", code=error.code
        ) from error
    except urllib.error.URLError as error:
        raise RenderError(f"render API unreachable: {error.reason}") from error


def render_batch(markups: list[str]) -> list[str]:
    """Render many markups in one request, one paragraph-separated block each.

    Falls back to one request per markup when the sentinel paragraphs do not
    split the answer back into exactly one chunk per markup.
    """
    if any(SENTINEL in markup for markup in markups):
        raise ValueError(f"markup contains the batch sentinel {SENTINEL!r}")
    document = "\n\n".join(
        f"{SENTINEL}{index:04d}\n\n{markup}"
        for index, markup in enumerate(markups)
    )
    parts = _SENTINEL_PARAGRAPH.split(render_one(document))
    # parts = [before, id0, chunk0, id1, chunk1, ...]
    ids = [int(part) for part in parts[1::2]]
    if parts[0].strip() or ids != list(range(len(markups))):
        return [render_one(markup) for markup in markups]
    return [chunk.strip() for chunk in parts[2::2]]


def refresh(
    needed: Iterable[str],
    snapshot: dict[str, str],
    *,
    render_all: bool = False,
    prune: bool = True,
    render: Callable[[list[str]], list[str]] = render_batch,
) -> tuple[dict[str, str], int]:
    """Return ``(new_snapshot, rendered_count)`` for the markups in ``needed``."""
    wanted = set(needed)
    todo = sorted(wanted if render_all else wanted - snapshot.keys())
    fresh = {m: h for m, h in snapshot.items() if m in wanted or not prune}
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        fresh.update(zip(batch, render(batch)))
        print(f"rendered {start + len(batch)}/{len(todo)}", file=sys.stderr)
    return fresh, len(todo)


def refresh_snapshot(
    old: Snapshot,
    local_pandoc: str | None,
    needed: Callable[[bool], Iterable[str]],
    *,
    render_all: bool = False,
    adopt: bool = False,
    render: Callable[[list[str]], list[str]] = render_batch,
) -> tuple[Snapshot, int, str | None]:
    """Return ``(new_snapshot, rendered_count, why_markdown_was_skipped)``.

    ``needed(markdown)`` lists the markups the oracle needs, with or without the
    Markdown cases. A skipped Markdown run renders only the other cases and
    prunes nothing, so it never discards entries it chose not to look at.
    """
    reason = markdown_skip_reason(local_pandoc, old.pandoc)
    markdown = reason is None or (adopt and local_pandoc is not None)
    renders, rendered = refresh(
        needed(markdown), old.renders, render_all=render_all, prune=markdown, render=render
    )
    version = local_pandoc if markdown else old.pandoc
    return Snapshot(version, renders), rendered, None if markdown else reason


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--all", action="store_true", help="render every markup again")
    parser.add_argument(
        "--adopt-pandoc",
        action="store_true",
        help="record this machine's pandoc version and drop Markdown entries it does not need",
    )
    args = parser.parse_args(argv)

    old = load_snapshot()
    try:
        new, rendered, skipped = refresh_snapshot(
            old,
            PANDOC_VERSION,
            lambda markdown: markup_cases.needed_markups(pandoc=markdown),
            render_all=args.all,
            adopt=args.adopt_pandoc,
        )
    except RenderError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2 if error.code == 401 else 1
    if skipped:
        print(
            f"Markdown cases skipped, no entries dropped: {skipped}"
            " (--adopt-pandoc records this pandoc instead)",
            file=sys.stderr,
        )
    if new != old:
        write_snapshot(new)
    print(
        f"snapshot: {len(new.renders)} entries, {rendered} rendered, "
        f"{len(set(old.renders) - set(new.renders))} dropped",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
