"""Cache directory helpers shared by search + write commands.

The cache directory is resolved exactly the same way as in ``search``:
    1. explicit --dir
    2. $CUBRID_JIRA_DIR
    3. ~/.local/share/cubrid-jira/issues/

The default server uses that directory directly for backward compatibility;
alternate servers use a hostname child directory to prevent key collisions.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from cubrid_jira.server import DEFAULT_SERVER, normalize_server

DEFAULT_DIR = Path.home() / ".local" / "share" / "cubrid-jira" / "issues"


def _server_namespace(server: str) -> str | None:
    normalized = normalize_server(server)
    hostname = urllib.parse.urlsplit(normalized).hostname
    if not hostname:
        raise ValueError(f"JIRA server must be an absolute URL: {server!r}")
    hostname = hostname.lower()
    default_hostname = urllib.parse.urlsplit(DEFAULT_SERVER).hostname
    if hostname == default_hostname:
        return None
    return hostname


def resolve_cache_dir(
    cli_dir: str | None = None,
    *,
    server: str = DEFAULT_SERVER,
) -> Path:
    if cli_dir:
        base = Path(cli_dir)
    else:
        env = os.environ.get("CUBRID_JIRA_DIR")
        base = Path(env) if env else DEFAULT_DIR

    namespace = _server_namespace(server)
    return base / namespace if namespace else base


def resolve_attachment_dir(
    key: str,
    cli_dir: str | None = None,
    *,
    server: str = DEFAULT_SERVER,
) -> Path:
    """Where attachments for ``key`` are downloaded.

    Same resolution convention as :func:`resolve_cache_dir` so
    ``$CUBRID_JIRA_DIR`` redirects *all* on-disk state, not just the issue
    cache:
        1. explicit --out (used as-is, no <KEY> suffix)
        2. $CUBRID_JIRA_DIR/attachments/[<alternate-host>/]<KEY>
        3. ~/.local/share/cubrid-jira/attachments/[<alternate-host>/]<KEY>
    """
    if cli_dir:
        return Path(cli_dir)
    env = os.environ.get("CUBRID_JIRA_DIR")
    base = Path(env) if env else DEFAULT_DIR.parent
    attachment_root = base / "attachments"
    namespace = _server_namespace(server)
    if namespace:
        attachment_root /= namespace
    return attachment_root / key


def resolve_field_map_path(
    cli_dir: str | None = None,
    *,
    server: str = DEFAULT_SERVER,
) -> Path:
    """Where the customfield name -> id map lives on disk.

    Co-located with the server-specific issues cache so field IDs from two
    Jira installations cannot overwrite one another.
    """
    return resolve_cache_dir(cli_dir, server=server) / "field-map.json"


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
