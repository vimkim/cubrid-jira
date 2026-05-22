"""Shared fixtures for cubrid-jira-fetcher tests."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import pytest


@pytest.fixture(autouse=True)
def _stub_credentials(monkeypatch):
    """Make sure auth.resolve_credentials() never touches the user's real netrc."""
    monkeypatch.setenv("CUBRID_JIRA_USER", "testuser")
    monkeypatch.setenv("CUBRID_JIRA_PASSWORD", "testpw")
    yield


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Skip the 1.5s backoff inside JiraClient retries so tests run fast."""
    import cubrid_jira.http as client_mod
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)
    yield


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

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
