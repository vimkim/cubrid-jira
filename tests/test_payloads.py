"""Pure payload-builder tests — no network, no filesystem."""

import pytest

from cubrid_jira.cli import (
    build_assignee_payload,
    build_comment_payload,
    build_comment_update_payload,
    build_create_payload,
    build_link_payload,
    build_transition_payload,
    build_update_payload,
    resolve_transition_id,
)
from cubrid_jira.fields import (
    AmbiguousFieldError,
    FieldSpecError,
    build_name_index,
    decode_field_value,
    is_custom_field_id,
    parse_field_spec,
    resolve_name,
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


def test_comment_update_payload():
    assert build_comment_update_payload("new body") == {"body": "new body"}


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


# --------------------------------------------------------------------------- #
# --field FIELD=VALUE parsing + name -> id resolution
# --------------------------------------------------------------------------- #

def test_parse_field_spec_simple():
    assert parse_field_spec("QA Scenario=Not applicable") == (
        "QA Scenario", "Not applicable"
    )


def test_parse_field_spec_keeps_equals_inside_value():
    # The first `=` splits; subsequent ones belong to the value.
    assert parse_field_spec("Notes=a=b=c") == ("Notes", "a=b=c")


def test_parse_field_spec_strips_name_whitespace():
    assert parse_field_spec("  Foo  =bar") == ("Foo", "bar")


def test_parse_field_spec_allows_empty_value():
    # Jira treats "" as a clear on some fields — don't reject it here.
    assert parse_field_spec("customfield_1=") == ("customfield_1", "")


def test_parse_field_spec_rejects_missing_equals():
    with pytest.raises(FieldSpecError, match="FIELD=VALUE"):
        parse_field_spec("nope")


def test_parse_field_spec_rejects_empty_name():
    with pytest.raises(FieldSpecError, match="empty"):
        parse_field_spec("=value")


def test_is_custom_field_id_recognises_raw_ids():
    assert is_custom_field_id("customfield_210565")
    assert is_custom_field_id("customfield_1")


def test_is_custom_field_id_rejects_other_strings():
    assert not is_custom_field_id("QA Scenario")
    assert not is_custom_field_id("summary")
    assert not is_custom_field_id("customfield_abc")
    assert not is_custom_field_id("CustomField_1")  # case-sensitive


def test_build_name_index_groups_ids_by_name():
    listing = [
        {"id": "customfield_1", "name": "QA Scenario"},
        {"id": "customfield_2", "name": "QA Scenario"},  # duplicate display name
        {"id": "customfield_3", "name": "Sprint"},
        {"id": "summary", "name": "Summary"},
        {"id": "", "name": "Bad"},  # ignored — missing id
        {"id": "customfield_4"},  # ignored — missing name
    ]
    index = build_name_index(listing)
    assert index == {
        "QA Scenario": ["customfield_1", "customfield_2"],
        "Sprint": ["customfield_3"],
        "Summary": ["summary"],
    }


def test_build_name_index_tolerates_garbage_entries():
    assert build_name_index([None, "string", {}, {"id": "x", "name": "ok"}]) == {
        "ok": ["x"],
    }


def test_resolve_name_unique_match():
    assert resolve_name("Sprint", {"Sprint": ["customfield_3"]}) == "customfield_3"


def test_resolve_name_miss_returns_none():
    assert resolve_name("Nope", {"Sprint": ["customfield_3"]}) is None


def test_resolve_name_ambiguous_raises():
    with pytest.raises(AmbiguousFieldError, match="ambiguous"):
        resolve_name(
            "QA Scenario",
            {"QA Scenario": ["customfield_1", "customfield_2"]},
        )


# --------------------------------------------------------------------------- #
# decode_field_value — string passthrough vs JSON parse for {…} / […]
# --------------------------------------------------------------------------- #

def test_decode_field_value_passes_text_through():
    assert decode_field_value("Not applicable") == "Not applicable"
    assert decode_field_value("") == ""


def test_decode_field_value_parses_select_object():
    # CUBRID's QA Scenario is a single-select; payload must be {"value": "..."}.
    assert decode_field_value('{"value":"Not Required"}') == {"value": "Not Required"}


def test_decode_field_value_parses_id_object():
    assert decode_field_value('{"id":"210606"}') == {"id": "210606"}


def test_decode_field_value_parses_array_for_multi_select():
    assert decode_field_value('[{"value":"a"},{"value":"b"}]') == [
        {"value": "a"}, {"value": "b"},
    ]


def test_decode_field_value_tolerates_leading_whitespace_before_json():
    assert decode_field_value('   {"value":"x"}') == {"value": "x"}


def test_decode_field_value_raises_on_broken_json():
    # Looks like JSON (starts with `{`) but malformed — must NOT silently
    # fall back to raw string; that would write garbage to the server.
    with pytest.raises(FieldSpecError, match="JSON"):
        decode_field_value('{"value": broken')


def test_decode_field_value_does_not_parse_number_or_bool():
    # Conservative: only object/array literals trigger JSON; bare numbers
    # and booleans are ambiguous with intended strings (e.g. version "1").
    assert decode_field_value("42") == "42"
    assert decode_field_value("true") == "true"


# --------------------------------------------------------------------------- #
# Payload builders with custom_fields
# --------------------------------------------------------------------------- #

def test_create_payload_with_custom_fields():
    out = build_create_payload(
        project="CBRD",
        issue_type="Bug",
        summary="x",
        custom_fields={"customfield_210565": "N/A"},
    )
    assert out == {
        "fields": {
            "project": {"key": "CBRD"},
            "summary": "x",
            "issuetype": {"name": "Bug"},
            "customfield_210565": "N/A",
        }
    }


def test_create_payload_custom_fields_merged_alongside_standard():
    out = build_create_payload(
        project="CBRD",
        issue_type="Bug",
        summary="x",
        priority="Major",
        custom_fields={
            "customfield_210565": "N/A",
            "customfield_210566": "rationale",
        },
    )
    f = out["fields"]
    assert f["priority"] == {"name": "Major"}
    assert f["customfield_210565"] == "N/A"
    assert f["customfield_210566"] == "rationale"


def test_update_payload_with_custom_fields_only():
    out = build_update_payload(
        custom_fields={"customfield_210565": "updated value"},
    )
    assert out == {"fields": {"customfield_210565": "updated value"}}


def test_update_payload_custom_and_standard_together():
    out = build_update_payload(
        summary="new title",
        custom_fields={"customfield_210565": "v"},
    )
    assert out == {
        "fields": {
            "summary": "new title",
            "customfield_210565": "v",
        }
    }
