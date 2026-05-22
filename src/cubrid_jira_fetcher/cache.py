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
