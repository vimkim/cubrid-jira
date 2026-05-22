"""Custom-field resolution for cubrid-jira create/update.

Parses ``--field FIELD=VALUE`` specs and maps display-name fields (e.g. "QA
Scenario") to their opaque ``customfield_NNN`` ids. The CLI handler is the
one that fetches ``/rest/api/2/field`` and hands the listing here; this
module only parses, caches to JSON on disk, and resolves names to ids.

Layering rule
-------------
PURE module — no urllib, no subprocess. Keep it that way so the layering
tests don't go yellow next refactor.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CUSTOMFIELD_RE = re.compile(r"^customfield_\d+$")


class FieldSpecError(ValueError):
    """Raised when a ``--field FIELD=VALUE`` string is malformed or unknown."""


class AmbiguousFieldError(ValueError):
    """Raised when a field display name maps to more than one id.

    The caller must surface the error so the user can re-run with one of
    the explicit ``customfield_NNN`` ids — silently picking one would risk
    writing to the wrong field.
    """


def parse_field_spec(spec: str) -> tuple[str, str]:
    """Split ``FIELD=VALUE`` into ``(name, value)``.

    Empty values are allowed (Jira treats ``""`` as a clear on some fields);
    empty names are not.
    """
    if "=" not in spec:
        raise FieldSpecError(
            f"--field must be FIELD=VALUE; got {spec!r}"
        )
    name, _, value = spec.partition("=")
    name = name.strip()
    if not name:
        raise FieldSpecError(
            f"--field FIELD half is empty in {spec!r}"
        )
    return name, value


def is_custom_field_id(name: str) -> bool:
    return bool(CUSTOMFIELD_RE.match(name))


def decode_field_value(raw: str) -> object:
    """Decode the VALUE half of ``--field FIELD=VALUE``.

    A leading ``{`` or ``[`` triggers a JSON parse so users can send
    select-list / cascading-select fields with the ``{"value": "..."}``
    shape JIRA expects. Anything else is preserved as a raw string —
    text fields and the common case keep working.
    """
    stripped = raw.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise FieldSpecError(
                f"--field VALUE looks like JSON but failed to parse: {e.msg}; "
                f"got {raw!r}"
            ) from e
    return raw


def build_name_index(field_listing: list[dict]) -> dict[str, list[str]]:
    """Group ids by display name from a ``/rest/api/2/field`` JSON list.

    A name can appear more than once when an admin has created two custom
    fields with the same display name — keep both ids so ``resolve_name``
    can raise the ambiguity error later.
    """
    index: dict[str, list[str]] = {}
    for f in field_listing or []:
        fid = f.get("id") if isinstance(f, dict) else None
        name = f.get("name") if isinstance(f, dict) else None
        if not fid or not name:
            continue
        index.setdefault(name, []).append(fid)
    return index


def resolve_name(name: str, index: dict[str, list[str]]) -> str | None:
    """Return the single field id for ``name`` or ``None`` on miss.

    Raises :class:`AmbiguousFieldError` when ``name`` matches more than one
    id; the caller must propagate so the user disambiguates by id.
    """
    ids = index.get(name)
    if not ids:
        return None
    if len(ids) > 1:
        raise AmbiguousFieldError(
            f"field name {name!r} is ambiguous: matches {ids!r}. "
            f"Re-run with the explicit id, e.g. --field {ids[0]}=..."
        )
    return ids[0]


def load_field_index(path: Path) -> dict[str, list[str]]:
    """Load the cached name -> [id] index. Empty dict on miss or bad JSON.

    A corrupt cache file degrades to a refresh rather than crashing — the
    next live call will rewrite it from ``/rest/api/2/field``.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: list(v)
        for k, v in data.items()
        if isinstance(v, list) and all(isinstance(x, str) for x in v)
    }


def save_field_index(path: Path, index: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
