"""Every command shown in the user docs must parse with the real CLI parser.

The docs copy flags from ``--help``. When a flag is renamed or removed, this
test fails instead of the docs going stale. Only ``sh``, ``bash``, and
``console`` fences are checked, and in a ``console`` fence only the lines that
start with ``$ ``. Put a synopsis with placeholders such as ``[--parent KEY]``
in a ``text`` fence.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from cubrid_jira import cli, kanban

ROOT = Path(__file__).resolve().parent.parent
DOCS = [
    "README.md",
    "docs/agent-guide.md",
    "docs/commands.md",
    "docs/development.md",
    "docs/output-and-exit-codes.md",
    "docs/recipes.md",
    "docs/setup.md",
]
CHECKED_FENCES = {"sh", "bash", "console"}
PROGRAMS = {"cubrid-jira", "cubrid-jira-kanban"}
SHELL_BREAKS = {"|", "||", "&", "&&", ";", "(", ")", ">", ">>", "<", "2>"}


def _shell_lines(text: str):
    fence = None
    pending = ""
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = None if fence is not None else stripped[3:].strip()
            pending = ""
            continue
        if fence not in CHECKED_FENCES:
            continue
        code = re.sub(r"(^|\s)#.*$", "", line).strip()
        if code.startswith("$ "):
            code = code[2:]
        elif fence == "console" and not pending:
            continue
        if code.endswith("\\"):
            pending += code[:-1] + " "
            continue
        yield lineno, pending + code
        pending = ""


def _at_command_start(tokens, i):
    if i == 0 or tokens[i - 1] in SHELL_BREAKS:
        return True
    return tokens[max(0, i - 2):i] == ["uv", "run"]


def _commands(text: str):
    for lineno, line in _shell_lines(text):
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
        for i, tok in enumerate(tokens):
            if tok not in PROGRAMS or not _at_command_start(tokens, i):
                continue
            argv = []
            for arg in tokens[i + 1:]:
                if arg in SHELL_BREAKS:
                    break
                argv.append(arg)
            yield lineno, tok, argv


def _examples():
    for rel in DOCS:
        path = ROOT / rel
        for lineno, prog, argv in _commands(path.read_text(encoding="utf-8")):
            yield pytest.param(prog, argv, id=f"{rel}:{lineno}")


class _Parsed(Exception):
    pass


def _parse_kanban(argv, monkeypatch):
    def stop(server):
        raise _Parsed

    monkeypatch.setattr(kanban, "resolve_credentials", stop)
    try:
        kanban.main(argv)
    except _Parsed:
        return
    except SystemExit as e:
        if e.code == 0:
            return
        pytest.fail(f"cubrid-jira-kanban {shlex.join(argv)} does not parse (exit {e.code})")
    pytest.fail("cubrid-jira-kanban returned without resolving credentials")


@pytest.mark.parametrize("prog,argv", list(_examples()))
def test_documented_command_parses(prog, argv, monkeypatch):
    if prog == "cubrid-jira-kanban":
        _parse_kanban(argv, monkeypatch)
        return
    try:
        cli._build_parser().parse_args(argv)
    except SystemExit as e:
        if e.code != 0:
            pytest.fail(f"cubrid-jira {shlex.join(argv)} does not parse (exit {e.code})")


@pytest.mark.parametrize("rel", DOCS)
def test_every_doc_has_checked_examples(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert list(_commands(text)), f"{rel}: no cubrid-jira command found in a sh fence"
