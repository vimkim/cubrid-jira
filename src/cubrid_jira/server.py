"""Jira server selection shared by CLI, HTTP, cache, and rendering layers."""

from __future__ import annotations

import urllib.parse

DEFAULT_SERVER = "http://jira.cubrid.org"


def normalize_server(server: str) -> str:
    """Return a base URL without trailing slashes."""
    return server.rstrip("/")


def server_from_browse_url(issue: str) -> str | None:
    """Return the Jira base URL encoded in a full ``.../browse/KEY`` URL."""
    parsed = urllib.parse.urlsplit(issue)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    marker = "/browse/"
    if marker not in parsed.path:
        return None
    prefix, _key = parsed.path.rsplit(marker, 1)
    return normalize_server(
        urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, prefix, "", ""))
    )


def select_server(issue: str, explicit_server: str | None = None) -> str:
    """Select a server, rejecting contradictory explicit and URL choices."""
    inferred = server_from_browse_url(issue)
    explicit = normalize_server(explicit_server) if explicit_server else None
    if explicit and inferred:
        explicit_host = urllib.parse.urlsplit(explicit).hostname
        inferred_host = urllib.parse.urlsplit(inferred).hostname
        if (
            explicit_host is None
            or inferred_host is None
            or explicit_host.lower() != inferred_host.lower()
        ):
            raise ValueError(
                f"--server {explicit!r} conflicts with browse URL server "
                f"{inferred!r}"
            )
    return explicit or inferred or DEFAULT_SERVER
