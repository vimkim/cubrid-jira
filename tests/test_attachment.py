"""attachment subcommand: metadata listing, gated download, manifest contract.

Network is never hit — ``search_issues`` is monkeypatched with a canned
response and ``download_file`` is stubbed (or routed through ``fake_server``),
mirroring ``test_jql``. The live path lives in ``test_live.py`` under
``-m live``.

Regression coverage for the 2026-07-30 review findings
(``docs/required-fixes-2026-07-30.md``): filename traversal (F2), stream-level
--max-bytes (F3), duplicate-name collisions (F4), OSError → manifest entry
(F5), no --server half-support (F6), anonymous fallback + 401 abort (F7),
$CUBRID_JIRA_DIR default dir (F9).
"""

from __future__ import annotations

import json

import pytest
from conftest import make_http_error

import cubrid_jira.cli as cli
from cubrid_jira import http
from cubrid_jira.cli import main
from cubrid_jira.http import JiraError, download_file

ATT_SMALL = {
    "id": "1",
    "filename": "repro.sql",
    "size": 100,
    "mimeType": "application/x-sql",
    "content": "http://jira.cubrid.org/secure/attachment/1/repro.sql",
}
ATT_BIG = {
    "id": "2",
    "filename": "core.tar.gz",
    "size": 50_000_000,
    "mimeType": "application/gzip",
    "content": "http://jira.cubrid.org/secure/attachment/2/core.tar.gz",
}


def _canned(attachments):
    return {"total": 1, "issues": [{"key": "CBRD-1", "fields": {"attachment": attachments}}]}


def _stub_download(monkeypatch, downloaded_urls=None, payload=b"x" * 100):
    def _fake_dl(url, dest, max_bytes=None):
        if downloaded_urls is not None:
            downloaded_urls.append(url)
        with open(dest, "wb") as fh:
            fh.write(payload)
        return len(payload)

    monkeypatch.setattr(cli, "download_file", _fake_dl)


def test_list_json_is_metadata_only(monkeypatch, capsys):
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([ATT_SMALL]))

    def _boom(url, dest, max_bytes=None):
        raise AssertionError("download must not run in --list mode")

    monkeypatch.setattr(cli, "download_file", _boom)

    main(["attachment", "CBRD-1", "--list", "--output", "json"])
    out = json.loads(capsys.readouterr().out)

    assert out["issue"] == "CBRD-1"
    assert out["count"] == 1
    a = out["attachments"][0]
    assert a["filename"] == "repro.sql"
    assert a["size"] == 100
    assert a["content"].endswith("/repro.sql")
    assert "downloaded" not in a  # list mode never reports download state


def test_download_gates_oversize(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([ATT_SMALL, ATT_BIG]))
    downloaded_urls = []
    _stub_download(monkeypatch, downloaded_urls)

    main([
        "attachment", "CBRD-1",
        "--out", str(tmp_path),
        "--max-bytes", "5000000",
        "--output", "json",
    ])
    out = json.loads(capsys.readouterr().out)
    by_name = {a["filename"]: a for a in out["attachments"]}

    assert by_name["repro.sql"]["downloaded"] is True
    assert (tmp_path / "repro.sql").exists()
    assert by_name["core.tar.gz"]["downloaded"] is False
    assert "oversize" in by_name["core.tar.gz"]["skipped"]
    # The oversize attachment is never fetched.
    assert downloaded_urls == [ATT_SMALL["content"]]


def test_no_attachments(monkeypatch, capsys):
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([]))
    main(["attachment", "CBRD-1", "--output", "json"])
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 0
    assert out["attachments"] == []


# --------------------------------------------------------------------------- #
# F2 — server-supplied filenames cannot escape the download directory.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "evil_name,safe_name",
    [
        ("../../evil.txt", "evil.txt"),          # relative traversal
        ("/etc/evil.txt", "evil.txt"),            # absolute (pathlib replaces lhs!)
        ("..\\..\\evil.txt", "evil.txt"),         # windows-style separators
        ("..", "attachment-9"),                   # degenerate names → id fallback
    ],
)
def test_download_sanitizes_traversal_filenames(
    monkeypatch, capsys, tmp_path, evil_name, safe_name
):
    att = {**ATT_SMALL, "id": "9", "filename": evil_name}
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([att]))
    _stub_download(monkeypatch)

    out_dir = tmp_path / "safe"
    main(["attachment", "CBRD-1", "--out", str(out_dir), "--output", "json"])
    out = json.loads(capsys.readouterr().out)

    entry = out["attachments"][0]
    assert entry["filename"] == safe_name
    assert entry["downloaded"] is True
    # Everything written stays inside out_dir; nothing escaped to tmp_path.
    assert (out_dir / safe_name).exists()
    written = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {out_dir / safe_name}


# --------------------------------------------------------------------------- #
# F4 — duplicate filenames (legal in Jira: attachments are id-keyed) must not
# silently clobber each other.
# --------------------------------------------------------------------------- #

def test_duplicate_filenames_get_id_suffix(monkeypatch, capsys, tmp_path):
    before = {**ATT_SMALL, "id": "11", "filename": "screenshot.png"}
    after = {**ATT_SMALL, "id": "12", "filename": "screenshot.png"}
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([before, after]))
    _stub_download(monkeypatch)

    main(["attachment", "CBRD-1", "--out", str(tmp_path), "--output", "json"])
    out = json.loads(capsys.readouterr().out)

    names = [a["filename"] for a in out["attachments"]]
    assert names == ["screenshot.png", "screenshot-12.png"]
    assert (tmp_path / "screenshot.png").exists()
    assert (tmp_path / "screenshot-12.png").exists()
    paths = {a["path"] for a in out["attachments"]}
    assert len(paths) == 2  # manifest never claims two downloads at one path


# --------------------------------------------------------------------------- #
# F3 — --max-bytes is enforced on received bytes, not the (untrusted,
# possibly absent) metadata size.
# --------------------------------------------------------------------------- #

def test_stream_cap_catches_missing_metadata_size(fake_server, capsys, tmp_path):
    att = {**ATT_SMALL, "size": None}  # server omits size → metadata gate passes
    # Specific route first: fake_server matches by suffix in registration
    # order, and the "" suffix of the search route matches every URL.
    fake_server.route("GET", "/secure/attachment/1/repro.sql", response=b"y" * 2000)
    fake_server.route("GET", "", response=_canned([att]))

    main([
        "attachment", "CBRD-1",
        "--out", str(tmp_path),
        "--max-bytes", "1000",
        "--output", "json",
    ])
    out = json.loads(capsys.readouterr().out)

    entry = out["attachments"][0]
    assert entry["downloaded"] is False
    assert "exceeded" in entry["skipped"]
    assert not (tmp_path / "repro.sql").exists()  # partial file removed


def test_download_file_within_cap_succeeds(fake_server, tmp_path):
    fake_server.route("GET", "/secure/attachment/1/repro.sql", response=b"y" * 500)
    dest = tmp_path / "repro.sql"
    written = download_file(ATT_SMALL["content"], str(dest), max_bytes=1000)
    assert written == 500
    assert dest.read_bytes() == b"y" * 500


# --------------------------------------------------------------------------- #
# F5 — filesystem failures become a manifest entry, not a traceback, and the
# one-JSON-object stdout contract survives.
# --------------------------------------------------------------------------- #

def test_oserror_is_reported_in_manifest(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([ATT_SMALL, ATT_BIG]))

    calls = []

    def _fail_first(url, dest, max_bytes=None):
        calls.append(url)
        if len(calls) == 1:
            raise JiraError(f"Filesystem error writing {dest}: disk full")
        with open(dest, "wb") as fh:
            fh.write(b"ok")
        return 2

    monkeypatch.setattr(cli, "download_file", _fail_first)

    main([
        "attachment", "CBRD-1",
        "--out", str(tmp_path),
        "--max-bytes", str(100_000_000),
        "--output", "json",
    ])
    out = json.loads(capsys.readouterr().out)  # stdout is still exactly one JSON object

    first, second = out["attachments"]
    assert first["downloaded"] is False
    assert "disk full" in first["skipped"]
    assert second["downloaded"] is True  # the loop carried on past the failure


def test_download_file_converts_oserror(fake_server, tmp_path):
    fake_server.route("GET", "/secure/attachment/1/repro.sql", response=b"data")
    missing_dir_dest = tmp_path / "does-not-exist" / "repro.sql"
    with pytest.raises(JiraError, match="Filesystem error"):
        download_file(ATT_SMALL["content"], str(missing_dir_dest))


# --------------------------------------------------------------------------- #
# F7 — download is a read: anonymous fallback without credentials, and a 401
# aborts the whole command (exit 2) instead of retrying per attachment.
# --------------------------------------------------------------------------- #

def test_download_falls_back_to_anonymous(fake_server, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(http, "resolve_credentials_optional", lambda *a, **k: None)
    fake_server.route("GET", "/secure/attachment/1/repro.sql", response=b"public")
    fake_server.route("GET", "", response=_canned([ATT_SMALL]))

    main(["attachment", "CBRD-1", "--out", str(tmp_path), "--output", "json"])
    out = json.loads(capsys.readouterr().out)

    assert out["attachments"][0]["downloaded"] is True
    auth_headers = [
        v for r in fake_server.requests
        for k, v in r.headers.items() if k.lower() == "authorization"
    ]
    assert auth_headers == []  # no credential was ever sent


def test_download_401_aborts_whole_command(fake_server, capsys, tmp_path):
    two = {**ATT_SMALL, "id": "2", "filename": "second.sql",
           "content": "http://jira.cubrid.org/secure/attachment/2/second.sql"}
    fake_server.route(
        "GET", "/secure/attachment/1/repro.sql",
        raise_=make_http_error(401, "auth failed"),
    )
    fake_server.route("GET", "", response=_canned([ATT_SMALL, two]))

    with pytest.raises(SystemExit) as ei:
        main(["attachment", "CBRD-1", "--out", str(tmp_path)])
    assert ei.value.code == 2
    # One metadata GET + ONE failed download — the second attachment is never
    # attempted with the rejected credential (CAPTCHA lockout footgun).
    download_reqs = [r for r in fake_server.requests if "/secure/attachment/" in r.url]
    assert len(download_reqs) == 1


# --------------------------------------------------------------------------- #
# F6 — --server was silently half-ignored (reads are hard-wired to JIRA_BASE);
# it must now be rejected loudly.
# --------------------------------------------------------------------------- #

def test_attachment_rejects_server_flag(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["attachment", "CBRD-1", "--server", "http://other.example.com"])
    assert ei.value.code == 2  # argparse usage error
    assert "--server" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# F9 — the default download dir honors $CUBRID_JIRA_DIR like all other state.
# --------------------------------------------------------------------------- #

def test_default_dir_honors_cubrid_jira_dir(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([ATT_SMALL]))
    _stub_download(monkeypatch)

    main(["attachment", "CBRD-1", "--output", "json"])
    out = json.loads(capsys.readouterr().out)

    expected = tmp_path / "attachments" / "CBRD-1" / "repro.sql"
    assert out["attachments"][0]["path"] == str(expected)
    assert expected.exists()
