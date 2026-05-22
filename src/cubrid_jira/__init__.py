"""cubrid-jira: CUBRID JIRA CLI.

Public API re-exports for stable imports across module reshuffles.
"""

from cubrid_jira.cli import (
    build_assignee_payload,
    build_comment_payload,
    build_create_payload,
    build_link_payload,
    build_transition_payload,
    main,
    resolve_transition_id,
)
from cubrid_jira.http import JiraClient, JiraError, fetch_issue, parse_issue_key

__all__ = [
    "JiraClient",
    "JiraError",
    "build_assignee_payload",
    "build_comment_payload",
    "build_create_payload",
    "build_link_payload",
    "build_transition_payload",
    "fetch_issue",
    "main",
    "parse_issue_key",
    "resolve_transition_id",
]
