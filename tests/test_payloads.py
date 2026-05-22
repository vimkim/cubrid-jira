"""Pure payload-builder tests — no network, no filesystem."""

import pytest

from cubrid_jira.cli import (
    build_assignee_payload,
    build_comment_payload,
    build_create_payload,
    build_link_payload,
    build_transition_payload,
    build_update_payload,
    resolve_transition_id,
)


def test_create_payload_minimal():
    out = build_create_payload(project="CBRD", issue_type="Bug", summary="hello")
    assert out == {
        "fields": {
            "project": {"key": "CBRD"},
            "summary": "hello",
            "issuetype": {"name": "Bug"},
        }
    }


def test_create_payload_full():
    out = build_create_payload(
        project="CBRD",
        issue_type="Bug",
        summary="hello",
        description="body text",
        priority="Major",
        assignee="vimkim",
        labels=["a", "b"],
        components=["sql", "broker"],
    )
    fields = out["fields"]
    assert fields["priority"] == {"name": "Major"}
    assert fields["assignee"] == {"name": "vimkim"}
    assert fields["labels"] == ["a", "b"]
    assert fields["components"] == [{"name": "sql"}, {"name": "broker"}]
    assert fields["description"] == "body text"


def test_link_payload_shape():
    out = build_link_payload("Relates", "CBRD-1", "CBRD-2")
    assert out == {
        "type": {"name": "Relates"},
        "inwardIssue": {"key": "CBRD-1"},
        "outwardIssue": {"key": "CBRD-2"},
    }


def test_comment_payload():
    assert build_comment_payload("note") == {"body": "note"}


def test_transition_payload_uses_id():
    assert build_transition_payload("31") == {"transition": {"id": "31"}}


def test_assignee_clear_sends_null():
    """`--to ""` is the documented way to unassign — must serialise to JSON null."""
    assert build_assignee_payload("") == {"name": None}
    assert build_assignee_payload(None) == {"name": None}


def test_assignee_set():
    assert build_assignee_payload("vimkim") == {"name": "vimkim"}


def test_update_payload_empty():
    """Builder is dumb — caller is responsible for refusing the empty case."""
    assert build_update_payload() == {"fields": {}}


def test_update_payload_summary_only():
    assert build_update_payload(summary="new title") == {
        "fields": {"summary": "new title"}
    }


def test_update_payload_description_only():
    assert build_update_payload(description="new body") == {
        "fields": {"description": "new body"}
    }


def test_update_payload_priority_only():
    assert build_update_payload(priority="Major") == {
        "fields": {"priority": {"name": "Major"}}
    }


def test_update_payload_labels_empty_list_clears():
    """`labels=[]` must serialise — it is the documented way to clear labels."""
    assert build_update_payload(labels=[]) == {"fields": {"labels": []}}


def test_update_payload_components_shape():
    assert build_update_payload(components=["server", "qa"]) == {
        "fields": {"components": [{"name": "server"}, {"name": "qa"}]}
    }


def test_update_payload_full():
    out = build_update_payload(
        summary="t",
        description="d",
        priority="Major",
        labels=["a", "b"],
        components=["sql"],
    )
    assert out == {
        "fields": {
            "summary": "t",
            "description": "d",
            "priority": {"name": "Major"},
            "labels": ["a", "b"],
            "components": [{"name": "sql"}],
        }
    }


def test_resolve_transition_case_insensitive():
    transitions = [
        {"id": "11", "name": "Open"},
        {"id": "21", "name": "In Progress"},
        {"id": "31", "name": "Resolved"},
    ]
    assert resolve_transition_id(transitions, "in progress") == "21"
    assert resolve_transition_id(transitions, "RESOLVED") == "31"


def test_resolve_transition_missing_raises():
    import pytest
    transitions = [{"id": "11", "name": "Open"}]
    with pytest.raises(ValueError, match="No transition"):
        resolve_transition_id(transitions, "Frobnicate")


def test_resolve_transition_ambiguous_raises():
    import pytest
    transitions = [
        {"id": "11", "name": "Done"},
        {"id": "12", "name": "done"},  # same after case-fold
    ]
    with pytest.raises(ValueError, match="[Aa]mbiguous"):
        resolve_transition_id(transitions, "done")
