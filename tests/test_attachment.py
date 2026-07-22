"""attachment subcommand: metadata listing, gated download, manifest contract.

Network is never hit — ``search_issues`` is monkeypatched with a canned
response and ``JiraClient.download`` is stubbed to write a local file, mirroring
``test_jql``. The live path lives in ``test_live.py`` under ``-m live``.
"""

from __future__ import annotations

import json

import cubrid_jira.cli as cli
from cubrid_jira.cli import main

ATT_SMALL = {
    "filename": "repro.sql",
    "size": 100,
    "mimeType": "application/x-sql",
    "content": "http://jira.cubrid.org/secure/attachment/1/repro.sql",
}
ATT_BIG = {
    "filename": "core.tar.gz",
    "size": 50_000_000,
    "mimeType": "application/gzip",
    "content": "http://jira.cubrid.org/secure/attachment/2/core.tar.gz",
}


def _canned(attachments):
    return {"total": 1, "issues": [{"key": "CBRD-1", "fields": {"attachment": attachments}}]}


def test_list_json_is_metadata_only(monkeypatch, capsys):
    monkeypatch.setattr(cli, "search_issues", lambda *a, **k: _canned([ATT_SMALL]))

    def _boom(self, url, dest):
        raise AssertionError("download must not run in --list mode")

    monkeypatch.setattr(cli.JiraClient, "download", _boom)

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

    def _fake_dl(self, url, dest):
        downloaded_urls.append(url)
        with open(dest, "wb") as fh:
            fh.write(b"x" * 100)
        return 100

    monkeypatch.setattr(cli.JiraClient, "download", _fake_dl)

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
