"""HTTP layer: JiraClient (authenticated mutations) + read helpers.

Layering rule
-------------
This module is the ONLY one that talks HTTP. It must not import ``subprocess``
or anything markdown-related — keep the layers clean so the rendering code
stays unit-testable without a network stack.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from cubrid_jira.auth import mask_password

JIRA_BASE = "http://jira.cubrid.org"
REST_API = f"{JIRA_BASE}/rest/api/2/issue"


class JiraError(RuntimeError):
    pass


def parse_issue_key(arg: str) -> str:
    """Extract an issue key (e.g. ``CBRD-26463``) from a URL or bare key."""
    m = re.search(r"([A-Z]+-\d+)", arg)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot parse issue key from: {arg!r}")


def basic_auth_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def fetch_issue(key: str) -> dict:
    """Unauthenticated GET for an issue's full JSON.

    Kept separate from :class:`JiraClient` because the read-only fetch flow
    must not require credentials — the public CUBRID JIRA happily serves
    issue JSON without auth.
    """
    url = f"{REST_API}/{key}?expand=renderedFields"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  [HTTP {e.code}] Failed to fetch {key}: {e.reason}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  [Error] Failed to fetch {key}: {e}", file=sys.stderr)
        return {}


class JiraClient:
    """Authenticated client for mutating endpoints + dry-run accounting.

    GET requests always execute, even in dry-run mode, so callers can resolve
    server state (e.g. a transition name → id) before printing the planned
    POST. Mutating verbs (POST/PUT/DELETE) are recorded but not sent in
    dry-run mode.

    The ``recorded_requests`` list captures every dry-run-suppressed
    mutation as ``{"method", "url", "body"}`` dicts so the CLI can emit a
    single structured ``--output json`` summary at the end.
    """

    def __init__(
        self,
        server: str,
        user: str,
        password: str,
        dry_run: bool = False,
        timeout: int = 20,
        output_format: str = "text",
    ) -> None:
        self.server = server.rstrip("/")
        self.user = user
        self.password = password
        self.dry_run = dry_run
        self.timeout = timeout
        self.output_format = output_format
        self.recorded_requests: list[dict] = []

    # -- public ---------------------------------------------------------- #

    def request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
    ) -> dict | None:
        url = self.server + path
        body_str: str | None = None
        body_bytes: bytes | None = None
        if body is not None:
            body_str = json.dumps(body, indent=2, ensure_ascii=False)
            body_bytes = body_str.encode("utf-8")

        is_mutation = method.upper() not in ("GET", "HEAD")
        if self.dry_run and is_mutation:
            self.recorded_requests.append(
                {"method": method.upper(), "url": url, "body": body}
            )
            if self.output_format == "text":
                self._print_dry_run_text(method, url, body_str)
            return None

        attempts = 0
        while True:
            attempts += 1
            try:
                return self._send(method, url, body_bytes)
            except urllib.error.HTTPError as e:
                code = e.code
                detail = _read_error_body(e)
                if code in (400, 401, 403, 404):
                    self._fail_http(code, path, detail)
                if 500 <= code < 600 and attempts < 2:
                    print(
                        f"Warning: server returned {code} for {method} {path}; "
                        "retrying once...",
                        file=sys.stderr,
                    )
                    time.sleep(1.5)
                    continue
                self._fail_http(code, path, detail)
                return None  # unreachable; _fail_http exits
            except urllib.error.URLError as e:
                reason = getattr(e, "reason", e)
                if attempts < 2:
                    print(
                        f"Warning: network error talking to {url}: {reason}; "
                        "retrying once...",
                        file=sys.stderr,
                    )
                    time.sleep(1.5)
                    continue
                raise JiraError(f"Network error talking to {url}: {reason}") from e

    # -- internals ------------------------------------------------------- #

    def _real_headers(self) -> dict[str, str]:
        return {
            "Authorization": basic_auth_header(self.user, self.password),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _masked_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Basic <base64({self.user}:{mask_password(self.password)})>",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _send(self, method: str, url: str, body_bytes: bytes | None) -> dict:
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=self._real_headers(),
            method=method.upper(),
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"_raw": raw.decode("utf-8", errors="replace")}

    def _print_dry_run_text(
        self, method: str, url: str, body_str: str | None
    ) -> None:
        print(
            "# DRY RUN — no request sent. Add --yes to perform the live write.",
            file=sys.stderr,
        )
        print(f"{method.upper()} {url}", file=sys.stderr)
        for k, v in self._masked_headers().items():
            print(f"  {k}: {v}", file=sys.stderr)
        if body_str is None:
            print("  (no body)", file=sys.stderr)
        else:
            print("Body:", file=sys.stderr)
            print(body_str)

    def _fail_http(self, code: int, path: str, body: str) -> None:
        if code == 401:
            print(
                "Error: Auth failed (HTTP 401).\n"
                "  Do NOT retry — Jira Server locks accounts and triggers a CAPTCHA\n"
                "  after repeated failed basic-auth attempts. Reset the CAPTCHA by\n"
                "  logging in via the web UI at the server URL, then re-check\n"
                "  CUBRID_JIRA_USER / CUBRID_JIRA_PASSWORD (or your ~/.netrc).",
                file=sys.stderr,
            )
            sys.exit(2)
        if code == 403:
            print(
                f"Error: HTTP 403. Authenticated, but missing permission for {path}.",
                file=sys.stderr,
            )
            if body:
                print(body, file=sys.stderr)
            sys.exit(3)
        if code == 404:
            print(
                f"Error: HTTP 404 for {path}. Check that the issue key exists "
                "and the URL path is correct.",
                file=sys.stderr,
            )
            if body:
                print(body, file=sys.stderr)
            sys.exit(4)
        if code == 400:
            print(
                f"Error: HTTP 400 for {path}. Server reported a validation problem:",
                file=sys.stderr,
            )
            if body:
                print(body, file=sys.stderr)
            sys.exit(5)
        print(f"Error: HTTP {code} for {path}.", file=sys.stderr)
        if body:
            print(body, file=sys.stderr)
        sys.exit(1)


def _read_error_body(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")
