from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import cubrid_jira.markdown as markdown
from conftest import PANDOC_HAS_JIRA


def test_markdown_to_jira_body_sanitizes_and_postprocesses(monkeypatch):
    captured = {}

    def fake_run(cmd, input, capture_output, text, check, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["check"] = check
        captured["timeout"] = timeout
        return SimpleNamespace(
            stdout="h1. Title\n{{code}}의 값\n*foo {{bar}} baz*\n"
        )

    monkeypatch.setattr(markdown.subprocess, "run", fake_run)

    body = markdown.markdown_to_jira_body(
        "소개`code`의 값\n# Title\n- item\n"
    )

    assert captured == {
        "cmd": ["pandoc", "--from", "markdown", "--to", "jira"],
        "input": "소개 `code` 의 값\n\n# Title\n\n- item\n",
        "capture_output": True,
        "text": True,
        "check": True,
        "timeout": 10,
    }
    assert "{{code}} 의 값" in body
    assert "*foo* {{bar}} *baz*" in body


@pytest.mark.parametrize(
    ("markdown_language", "jira_language"),
    [
        ("text", "none"),
        ("plaintext", "none"),
        ("mermaid", "none"),
        ("shell", "sh"),
        ("sql", "sql"),
    ],
)
@pytest.mark.skipif(not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats")
def test_markdown_to_jira_body_normalizes_code_formatter_languages(
    markdown_language, jira_language
):
    body = markdown.markdown_to_jira_body(
        f"```{markdown_language}\nbody\n```\n"
    )

    assert f"{{code:{jira_language}}}" in body
    if markdown_language != jira_language:
        assert f"{{code:{markdown_language}}}" not in body


def test_markdown_to_jira_body_raises_when_pandoc_is_missing(monkeypatch):
    def fail(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(markdown.subprocess, "run", fail)

    with pytest.raises(
        markdown.MarkdownConversionError,
        match="pandoc is not installed",
    ):
        markdown.markdown_to_jira_body("# Title\n")


def test_markdown_to_jira_body_raises_when_pandoc_fails(monkeypatch):
    def fail(cmd, **_kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="bad markdown")

    monkeypatch.setattr(markdown.subprocess, "run", fail)

    with pytest.raises(
        markdown.MarkdownConversionError,
        match="pandoc failed: bad markdown",
    ):
        markdown.markdown_to_jira_body("# Title\n")


def test_markdown_to_jira_body_rejects_simple_tables_before_they_can_split_cells():
    body = (
        "  ID   내용                                                                 분류\n"
        "  ---- -------------------------------------------------------------------- ------\n"
        "  N1   아주 긴 셀 내용이 대시 폭을 넘어가면 "
        "has_dealloc_prevent_flag 같은 식별자가 쪼개진다   High\n"
    )

    with pytest.raises(
        markdown.MarkdownConversionError,
        match=r"simple table.*line 2.*pipe table",
    ):
        markdown.markdown_to_jira_body(body)


def test_jira_to_markdown_converts_when_pandoc_succeeds(monkeypatch):
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        return SimpleNamespace(returncode=0, stdout="## Title\n", stderr="")

    monkeypatch.setattr(markdown.subprocess, "run", fake_run)

    assert markdown.jira_to_markdown("h2. Title\n") == "## Title"
    assert captured["cmd"] == [
        "pandoc", "-f", "jira", "-t",
        "markdown-simple_tables-multiline_tables-grid_tables+pipe_tables",
        "--wrap=none",
    ]
    assert captured["input"] == "h2. Title\n"


@pytest.mark.skipif(not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats")
def test_jira_markdown_jira_round_trip_uses_pipe_tables_and_preserves_cells():
    jira = (
        "||ID||내용||분류||\n"
        "|N1|{{has_dealloc_prevent_flag}} :10772 한글 내용|High|\n"
        "|A3|{{\\[PGBUF\\] page buffer}} 설명|Medium|\n"
    )

    body = markdown.jira_to_markdown(jira)

    assert body.startswith("| ID")
    assert "| N1" in body
    assert "has_dealloc_prevent_flag" in body
    assert ":10772" in body
    assert "| A3" in body
    assert "`[PGBUF] page buffer`" in body

    round_tripped = markdown.markdown_to_jira_body(body)
    assert "| N1 | {{has_dealloc_prevent_flag}} :10772 한글 내용 | High |" in round_tripped
    assert "| A3 | {{\\[PGBUF\\] page buffer}} 설명 | Medium |" in round_tripped


@pytest.mark.skipif(not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats")
def test_markdown_jira_markdown_round_trip_preserves_escaped_pipes_in_cells():
    body = (
        "| Name | Flags |\n"
        "|---|---|\n"
        "| DWB | (PRM_FOR_SERVER \\| PRM_USER_CHANGE \\| PRM_SIZE_UNIT) |\n"
    )

    jira = markdown.markdown_to_jira_body(body)

    assert "PRM_FOR_SERVER &#124; PRM_USER_CHANGE &#124; PRM_SIZE_UNIT" in jira
    assert "&bsol;" not in jira

    round_tripped = markdown.jira_to_markdown(jira)
    assert "PRM_FOR_SERVER \\| PRM_USER_CHANGE \\| PRM_SIZE_UNIT" in round_tripped


@pytest.mark.skipif(not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats")
def test_markdown_to_jira_body_preserves_fenced_code_content_byte_for_byte():
    body = "```diff\n\tcontext\n+ added\n context\n```\n"

    jira = markdown.markdown_to_jira_body(body)

    assert jira == "{code:none}\n\tcontext\n+ added\n context\n{code}\n"


@pytest.mark.skipif(not PANDOC_HAS_JIRA, reason="pandoc lacks Jira formats")
def test_simple_table_shaped_text_inside_longer_code_fence_is_verbatim():
    body = (
        "````text\n"
        "```\n"
        "  ID   Content\n"
        "  ---- -------\n"
        "````\n"
    )

    jira = markdown.markdown_to_jira_body(body)

    assert jira == (
        "{code:none}\n"
        "```\n"
        "  ID   Content\n"
        "  ---- -------\n"
        "{code}\n"
    )


def test_jira_to_markdown_falls_back_to_raw_when_pandoc_lacks_jira(
    monkeypatch, capsys
):
    # pandoc < 2.9.1 has no jira reader: it exits non-zero with empty stdout.
    # Returning that stdout would make every issue look description-less.
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1, stdout="", stderr="Unknown reader: jira\n"
        )

    monkeypatch.setattr(markdown.subprocess, "run", fake_run)
    monkeypatch.setattr(markdown, "_pandoc_read_warned", False)

    body = "h2. Title\n\n||h||\n|cell|\n"
    assert markdown.jira_to_markdown(body) == body
    assert "Unknown reader: jira" in capsys.readouterr().err


def test_jira_to_markdown_warns_only_once(monkeypatch, capsys):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(markdown.subprocess, "run", fake_run)
    monkeypatch.setattr(markdown, "_pandoc_read_warned", False)

    for _ in range(3):
        markdown.jira_to_markdown("h2. Title\n")

    assert capsys.readouterr().err.count("Warning: pandoc") == 1


def test_jira_to_markdown_falls_back_when_pandoc_is_missing(monkeypatch):
    def fail(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(markdown.subprocess, "run", fail)

    assert markdown.jira_to_markdown("h2. Title\n") == "h2. Title\n"
