"""The CLI talks HTTPS directly and never lets a redirect carry a write."""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cubrid_jira.cli import main
from cubrid_jira.http import JiraClient


def test_default_jql_search_goes_straight_to_https(fake_server):
    jql = "project = CBRD AND resolution = Duplicate"
    fake_server.route("GET", "", response={"total": 0, "issues": []})

    main(["jql", jql, "--output", "json"])

    url = fake_server.requests[-1].url
    assert url.startswith("https://jira.cubrid.org/rest/api/2/search?")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["jql"] == [jql]


class _RecordingHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _drain_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

    def _handle(self):
        self._drain_body()
        self.server.seen.append((self.command, self.path))
        location = self.server.redirect_to
        if location is None:
            payload = json.dumps({"applied": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(302)
        self.send_header("Location", location + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_PUT = do_DELETE = _handle


def _start(redirect_to: str | None) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.seen = []
    server.redirect_to = redirect_to
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _base(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def redirected(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("NO_PROXY", "*")
    sink = _start(None)
    redirector = _start(_base(sink))
    yield _base(redirector), _base(sink), sink
    for server in (redirector, sink):
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/rest/api/2/issue/CBRD-1/comment", {"body": "x"}),
        ("PUT", "/rest/api/2/issue/CBRD-1", {"fields": {}}),
    ],
)
def test_redirected_write_is_refused(redirected, capsys, method, path, body):
    redirector_base, sink_base, sink = redirected
    client = JiraClient(redirector_base, "u", "p", dry_run=False)

    with pytest.raises(SystemExit) as exc:
        client.request(method, path, body)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert sink_base + path in err
    assert f"--server {sink_base}" in err
    assert not [m for m, _ in sink.seen if m in ("POST", "PUT")]
