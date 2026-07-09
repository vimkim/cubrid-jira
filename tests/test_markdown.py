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
