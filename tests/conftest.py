"""Shared fixtures for cubrid-jira-fetcher tests."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import pytest

from cubrid_jira.auth import resolve_credentials_optional


def _pandoc_has_jira_reader_and_writer() -> bool:
    if shutil.which("pandoc") is None:
        return False
    try:
        for flag in ("--list-input-formats", "--list-output-formats"):
            result = subprocess.run(
                ["pandoc", flag],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            if "jira" not in result.stdout.splitlines():
                return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


PANDOC_HAS_JIRA = _pandoc_has_jira_reader_and_writer()


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch):
    """Make sure auth.resolve_credentials() never touches the user's real netrc.

    resolve_credentials_optional is memoized per process (one netrc parse per
    run), so the cache must be reset around every test or the first test's
    credentials would leak into the rest of the suite.
    """
    resolve_credentials_optional.cache_clear()
    monkeypatch.setenv("CUBRID_JIRA_USER", "testuser")
    monkeypatch.setenv("CUBRID_JIRA_PASSWORD", "testpw")
    yield
    resolve_credentials_optional.cache_clear()


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Skip the 1.5s backoff inside JiraClient retries so tests run fast."""
    import cubrid_jira.http as client_mod
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    yield


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        # Supports chunked reads (read(65536)) for download streaming as well
        # as the read-everything calls the JSON helpers use.
        if size is None or size < 0:
            chunk = self._body[self._pos:]
            self._pos = len(self._body)
        else:
            chunk = self._body[self._pos:self._pos + size]
            self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: dict
    body: bytes | None


@dataclass
class FakeJiraServer:
    """A pluggable fake for ``urllib.request.urlopen``.

    Use ``server.route(method, suffix, response=..., raise_=..., status=...)``
    to register canned responses. ``urlopen`` matches by suffix so tests don't
    have to repeat the base URL.
    """

    routes: list = field(default_factory=list)
    requests: list[RecordedRequest] = field(default_factory=list)

    def route(self, method: str, suffix: str, *, response=None, raise_=None, status: int = 200):
        self.routes.append((method.upper(), suffix, response, raise_, status))

    def urlopen(self, req, timeout=None):  # signature must match stdlib
        method = req.get_method().upper()
        url = req.full_url
        body = req.data
        # CookieJar.add_cookie_header() writes Cookie to unredirected_hdrs,
        # not headers — merge both so tests can assert cookie continuity.
        merged = dict(req.headers)
        merged.update(req.unredirected_hdrs)
        self.requests.append(
            RecordedRequest(method=method, url=url, headers=merged, body=body)
        )
        for r_method, suffix, response, raise_, status in self.routes:
            if r_method != method:
                continue
            if not url.endswith(suffix):
                continue
            if raise_ is not None:
                if isinstance(raise_, urllib.error.HTTPError):
                    raise raise_
                raise raise_
            payload: bytes
            if response is None:
                payload = b""
            elif isinstance(response, (dict, list)):
                payload = json.dumps(response).encode("utf-8")
            elif isinstance(response, bytes):
                payload = response
            else:
                payload = str(response).encode("utf-8")
            return _FakeResponse(payload)
        raise AssertionError(f"Unexpected request: {method} {url}")


@pytest.fixture
def fake_server(monkeypatch):
    server = FakeJiraServer()
    monkeypatch.setattr(urllib.request, "urlopen", server.urlopen)
    return server


def make_http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    """Build an HTTPError with a readable body, like the stdlib raises."""
    return urllib.error.HTTPError(
        url="http://jira.cubrid.org/whatever",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )
