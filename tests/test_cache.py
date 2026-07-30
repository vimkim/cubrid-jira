"""Cache helpers: resolve + invalidate."""

from pathlib import Path

from cubrid_jira.cache import invalidate, resolve_attachment_dir, resolve_cache_dir


def test_resolve_cli_arg_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path / "from-env"))
    assert resolve_cache_dir(str(tmp_path / "from-cli")) == tmp_path / "from-cli"


def test_resolve_env_wins_over_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path / "from-env"))
    assert resolve_cache_dir(None) == tmp_path / "from-env"


def test_attachment_dir_cli_arg_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path / "from-env"))
    got = resolve_attachment_dir("CBRD-1", str(tmp_path / "from-cli"))
    assert got == tmp_path / "from-cli"  # --out is used as-is, no <KEY> suffix


def test_attachment_dir_env_wins_over_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CUBRID_JIRA_DIR", str(tmp_path / "from-env"))
    got = resolve_attachment_dir("CBRD-1", None)
    assert got == tmp_path / "from-env" / "attachments" / "CBRD-1"


def test_attachment_dir_default(monkeypatch):
    monkeypatch.delenv("CUBRID_JIRA_DIR", raising=False)
    got = resolve_attachment_dir("CBRD-1", None)
    assert got == Path.home() / ".local" / "share" / "cubrid-jira" / "attachments" / "CBRD-1"


def test_invalidate_removes_md_and_json(tmp_path):
    (tmp_path / "CBRD-1.md").write_text("hi")
    (tmp_path / "CBRD-1.json").write_text("{}")
    (tmp_path / "CBRD-2.md").write_text("other")
    assert invalidate("CBRD-1", tmp_path) == 2
    assert not (tmp_path / "CBRD-1.md").exists()
    assert not (tmp_path / "CBRD-1.json").exists()
    # Doesn't touch unrelated keys.
    assert (tmp_path / "CBRD-2.md").exists()


def test_invalidate_is_prefix_safe(tmp_path):
    """CBRD-1 must NOT match CBRD-10."""
    (tmp_path / "CBRD-1.md").write_text("a")
    (tmp_path / "CBRD-10.md").write_text("b")
    assert invalidate("CBRD-1", tmp_path) == 1
    assert (tmp_path / "CBRD-10.md").exists()


def test_invalidate_missing_dir_is_noop(tmp_path):
    assert invalidate("CBRD-1", tmp_path / "does-not-exist") == 0
