from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

import cubrid_jira.markdown as markdown


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
    ("pandoc_language", "jira_language"),
    [
        ("text", "none"),
        ("plaintext", "none"),
        ("mermaid", "none"),
        ("shell", "sh"),
        ("sql", "sql"),
    ],
)
def test_markdown_to_jira_body_normalizes_code_formatter_languages(
    monkeypatch, pandoc_language, jira_language
):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout=f"{{code:{pandoc_language}}}\nbody\n{{code}}\n"
        )

    monkeypatch.setattr(markdown.subprocess, "run", fake_run)

    body = markdown.markdown_to_jira_body("```text\nbody\n```\n")

    assert f"{{code:{jira_language}}}" in body
    if pandoc_language != jira_language:
        assert f"{{code:{pandoc_language}}}" not in body


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


def test_jira_to_markdown_converts_when_pandoc_succeeds(monkeypatch):
    captured = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        return SimpleNamespace(returncode=0, stdout="## Title\n", stderr="")

    monkeypatch.setattr(markdown.subprocess, "run", fake_run)

    assert markdown.jira_to_markdown("h2. Title\n") == "## Title"
    assert captured["cmd"] == [
        "pandoc", "-f", "jira", "-t", "markdown", "--wrap=none",
    ]
    assert captured["input"] == "h2. Title\n"


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
