"""Deprecated alias for :mod:`cubrid_jira`.

Importing this package emits a :class:`DeprecationWarning` and re-exports the
public API of :mod:`cubrid_jira`. New code should ``import cubrid_jira``
directly.

This shim exists so a single rename in v1.0 does not break out-of-tree
callers; it will be removed in a future major release.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "`cubrid_jira_fetcher` has been renamed to `cubrid_jira`; "
    "update your imports — this shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

from cubrid_jira import *  # noqa: F401, F403, E402
from cubrid_jira import __all__  # noqa: E402
