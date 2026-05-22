"""Session-cookie HTTP client for the JIRA Server Convert wizard.

The wizard endpoints under ``/secure/*.jspa`` require a real
``JSESSIONID`` cookie (basic auth alone is not enough), and the commit
POSTs require ``X-Atlassian-Token: no-check`` even with a valid
``atl_token`` form field. See
``docs/reparent-subtasks-via-convert-wizard.md`` for the full field
report.

Layering rule
-------------
This module is allowed to talk HTTP (mirrors :mod:`cubrid_jira.http`).
It must not import ``subprocess`` and it must not depend on the markdown
rendering layer. Cookie state is managed manually via
:mod:`http.cookiejar` rather than ``HTTPCookieProcessor`` so that test
monkeypatches of ``urllib.request.urlopen`` still intercept every call.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from cubrid_jira.auth import mask_password


class SessionAuthError(RuntimeError):
    """Raised when wizard endpoints reject the session cookies."""


class WizardHTTPError(RuntimeError):
    """Non-2xx response from a wizard endpoint."""

    def __init__(self, code: int, url: str, body: str) -> None:
        super().__init__(f"HTTP {code} for {url}: {body[:300]}")
        self.code = code
        self.url = url
        self.body = body


class SessionClient:
    """Cookie-bearing HTTP client for ``/secure/*.jspa`` wizard endpoints.

    Mirrors :class:`cubrid_jira.http.JiraClient` semantics:

    * ``dry_run=True`` (the default for wizard CLI commands without
      ``--yes``) records every mutating POST in :attr:`recorded_requests`
      and skips sending. GETs are not used during dry-run by callers.
    * The ``recorded_requests`` entries have the same
      ``{"method", "url", "body"}`` shape used by ``JiraClient`` so the
      existing CLI ``_emit`` helper for ``--output json`` works
      unchanged.
    * On HTTP 401 we exit immediately with code 2, never retrying — Jira
      Server triggers a CAPTCHA after repeated failed basic-auth.
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
        self.cookies: CookieJar = CookieJar()
        self.recorded_requests: list[dict] = []
        self._logged_in = False

    # ----- session lifecycle ------------------------------------------- #

    def login(self) -> None:
        """Warm the session + authenticate. Idempotent.

        Two-step: a warm-up GET makes JIRA hand us a ``JSESSIONID``, then
        a JSON POST to ``/rest/auth/1/session`` upgrades it to an
        authenticated session.
        """
        if self._logged_in:
            return
        self._raw("GET", self.server + "/secure/Dashboard.jspa")
        body = json.dumps(
            {"username": self.user, "password": self.password}
        ).encode("utf-8")
        self._raw(
            "POST",
            self.server + "/rest/auth/1/session",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._logged_in = True

    # ----- HTML helpers ------------------------------------------------ #

    def html_get(self, path: str) -> str:
        """GET a wizard page and return its HTML body."""
        return self._raw("GET", self.server + path)

    def html_post(self, path: str, fields: dict[str, str]) -> str:
        """POST a wizard form step.

        In dry-run mode this records the request and returns ``""`` —
        downstream ``check_xsrf`` and ``parse_form`` on an empty string
        are harmless no-ops, which lets the driver keep its linear
        shape in both modes.
        """
        url = self.server + path
        if self.dry_run:
            self.recorded_requests.append(
                {"method": "POST", "url": url, "body": dict(fields)}
            )
            if self.output_format == "text":
                self._print_dry_run_text(url, fields)
            return ""
        data = urllib.parse.urlencode(fields).encode("utf-8")
        return self._raw(
            "POST",
            url,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Atlassian-Token": "no-check",
            },
        )

    # ----- internals --------------------------------------------------- #

    def _raw(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        req = urllib.request.Request(url, data=data, method=method.upper())
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        self.cookies.add_cookie_header(req)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                _safe_extract_cookies(self.cookies, resp, req)
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                _fail_401()
            body = _read_error_body(e)
            raise WizardHTTPError(e.code, url, body) from e
        except urllib.error.URLError as e:
            raise WizardHTTPError(0, url, str(getattr(e, "reason", e))) from e
        if not raw:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")

    def _print_dry_run_text(self, url: str, fields: dict[str, str]) -> None:
        print(
            "# DRY RUN — no request sent. Add --yes to perform the live write.",
            file=sys.stderr,
        )
        print(f"POST {url}", file=sys.stderr)
        print("  Content-Type: application/x-www-form-urlencoded", file=sys.stderr)
        print("  X-Atlassian-Token: no-check", file=sys.stderr)
        print(
            "  Cookie: <session cookies set at runtime; "
            f"user={self.user} pw={mask_password(self.password)}>",
            file=sys.stderr,
        )
        print("Body:", file=sys.stderr)
        print(json.dumps(fields, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Helpers (module-private).
# --------------------------------------------------------------------------- #

def _safe_extract_cookies(jar: CookieJar, resp, req) -> None:
    """Tolerate test fakes that don't fully implement ``addinfourl``."""
    try:
        jar.extract_cookies(resp, req)
    except (AttributeError, TypeError):
        pass


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


def _fail_401() -> None:
    print(
        "Error: Auth failed (HTTP 401).\n"
        "  Do NOT retry — Jira Server locks accounts and triggers a CAPTCHA\n"
        "  after repeated failed basic-auth attempts. Reset the CAPTCHA by\n"
        "  logging in via the web UI at the server URL, then re-check\n"
        "  CUBRID_JIRA_USER / CUBRID_JIRA_PASSWORD (or your ~/.netrc).",
        file=sys.stderr,
    )
    sys.exit(2)
