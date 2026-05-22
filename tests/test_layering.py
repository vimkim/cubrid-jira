"""Layering rules enforced by static import inspection.

- ``cubrid_jira.markdown`` may not import ``urllib`` — networking lives in
  http.py.
- ``cubrid_jira.http`` may not import ``subprocess`` — process spawning
  (pandoc) lives in markdown.py.

If either rule breaks, the next refactor will silently couple the layers
again; this is a cheap guardrail.
"""

from __future__ import annotations

import ast
from importlib import resources


def _imports_of(module: str) -> set[str]:
    src = resources.files("cubrid_jira").joinpath(module + ".py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_markdown_does_not_import_urllib():
    names = _imports_of("markdown")
    assert not any(n == "urllib" or n.startswith("urllib.") for n in names), (
        f"cubrid_jira.markdown must not import urllib; found: {sorted(names)}"
    )


def test_http_does_not_import_subprocess():
    names = _imports_of("http")
    assert "subprocess" not in names, (
        f"cubrid_jira.http must not import subprocess; found: {sorted(names)}"
    )


def test_back_compat_shim_warns_and_reexports():
    import warnings

    # Re-import via importlib so the warning fires fresh in this test process.
    import importlib
    import sys
    sys.modules.pop("cubrid_jira_fetcher", None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mod = importlib.import_module("cubrid_jira_fetcher")
    msgs = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("cubrid_jira_fetcher" in m and "cubrid_jira" in m for m in msgs), msgs
    # Public surface re-exported.
    assert callable(mod.main)
    assert callable(mod.parse_issue_key)
