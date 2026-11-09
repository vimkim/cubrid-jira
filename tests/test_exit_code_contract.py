"""Failures end with an `Error:` line and the documented exit code.

0 ok | 1 generic | 2 HTTP 401 | 3 HTTP 403 | 4 HTTP 404 | 5 HTTP 400.
Scripts and agents branch on the code, so no failure may escape as a
traceback or collapse into the wrong code.
"""

from __future__ import annotations

import pytest

from cubrid_jira.cli import main


@pytest.mark.parametrize(
    "argv, bad",
    [
        (["comment", "CBRD-XXXXX", "--body-file", "note.md"], "CBRD-XXXXX"),
        (["link", "CBRD-1", "--type", "Relates", "--to", "nonsense"], "nonsense"),
        (["transition", "not-a-key"], "not-a-key"),
    ],
)
def test_unparseable_issue_key_exits_1_before_any_request(
    argv, bad, fake_server, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.md").write_text("body\n", encoding="utf-8")

    with pytest.raises(SystemExit) as ei:
        main(argv)

    err = capsys.readouterr().err
    assert ei.value.code == 1
    assert err.startswith("Error:")
    assert bad in err
    assert "Traceback" not in err
    assert fake_server.requests == []
