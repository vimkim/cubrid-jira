"""Cache directory helpers shared by search + write commands.

The cache directory is resolved exactly the same way as in ``search``:
    1. explicit --dir
    2. $CUBRID_JIRA_DIR
    3. ~/.local/share/cubrid-jira/issues/
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DIR = Path.home() / ".local" / "share" / "cubrid-jira" / "issues"


def resolve_cache_dir(cli_dir: str | None = None) -> Path:
    if cli_dir:
        return Path(cli_dir)
    env = os.environ.get("CUBRID_JIRA_DIR")
    if env:
        return Path(env)
    return DEFAULT_DIR


def resolve_attachment_dir(key: str, cli_dir: str | None = None) -> Path:
    """Where attachments for ``key`` are downloaded.

    Same resolution convention as :func:`resolve_cache_dir` so
    ``$CUBRID_JIRA_DIR`` redirects *all* on-disk state, not just the issue
    cache:
        1. explicit --out (used as-is, no <KEY> suffix)
        2. $CUBRID_JIRA_DIR/attachments/<KEY>
        3. ~/.local/share/cubrid-jira/attachments/<KEY>
    """
    if cli_dir:
        return Path(cli_dir)
    env = os.environ.get("CUBRID_JIRA_DIR")
    if env:
        return Path(env) / "attachments" / key
    return DEFAULT_DIR.parent / "attachments" / key


def resolve_field_map_path(cli_dir: str | None = None) -> Path:
    """Where the customfield name -> id map lives on disk.

    Co-located with the issues cache so $CUBRID_JIRA_DIR / --dir isolation
    in tests covers the field-map cache for free.
    """
    return resolve_cache_dir(cli_dir) / "field-map.json"


def invalidate(key: str, directory: Path) -> int:
    """Delete cached files for ``key``. Returns number of files removed."""
    if not directory.exists():
        return 0
    deleted = 0
    for pattern in (f"{key}.md", f"{key}.json", f"{key}-*.md", f"{key}-*.json"):
        for path in directory.glob(pattern):
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
    return deleted
