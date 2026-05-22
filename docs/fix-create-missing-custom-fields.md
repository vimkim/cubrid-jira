# Prompt: Fix `cubrid-jira create` failing on required custom fields

> **Status (2026-05-22): RESOLVED.** See the "Resolution notes" section at the bottom for what was actually built and one important deviation from the original spec (`QA Scenario` is a single-select field, not text — so the recommended value shape is JSON, not raw string).


## TL;DR for the implementing agent

`cubrid-jira create` currently has no way to set JIRA custom fields. The CUBRID JIRA project gates issue creation on `customfield_210565` (QA Scenario), so every `create` call against `CBRD` fails with HTTP 400. Add a way to set custom fields from the CLI — and while you're in there, confirm/ensure the create subcommand prints the newly-created issue key to stdout (under `--output json`) so callers can chain on it.

This is two related fixes in one task, because they are usually hit together: a successful create that doesn't return the key is almost as useless as a failed create.

---

## Observed bug (concrete repro)

Command (with valid credentials in env / .netrc):

```bash
cubrid-jira create \
  --project CBRD \
  --type "Improve Function/Performance" \
  --summary "[VACUUM] 서버 가동 중(CS 모드)에서 vacuumdb 유틸리티로 vacuum 트리거 지원" \
  --description-file /path/to/draft.md \
  --link-relates CBRD-26720 \
  --output json \
  --yes
```

Result:

```
Error: HTTP 400 for /rest/api/2/issue. Server reported a validation problem:
{"errorMessages":[],"errors":{"customfield_210565":"QA Scenario is required."}}
```

The dry-run output (which only shows the resolved request body) confirms the request payload contains no entry for `customfield_210565`:

```json
{
  "fields": {
    "project": { "key": "CBRD" },
    "summary": "...",
    "issuetype": { "name": "Improve Function/Performance" },
    "description": "..."
  }
}
```

So the bug is unambiguously CLI-side: the tool has no path to populate this field.

`grep -rE "customfield|qa_scenario" $(cubrid-jira ... source dir)` returns nothing — confirming there is no hidden flag, env-var fallback, or hardcoded default in the current build.

---

## Why this matters

- Every first-time create against the CUBRID project fails. Users are forced to (a) create via the web UI, then (b) switch back to CLI for everything else — defeating the point of having a CLI.
- The failure is not a config issue users can fix locally; the CLI literally cannot emit the required field.
- This is not specific to QA Scenario — any JIRA project that requires *any* custom field will block `cubrid-jira create` the same way. The fix should be general, not a one-off `--qa-scenario` flag.

---

## Suggested fix — preferred approach

Add a general-purpose flag for arbitrary custom fields:

```
--field FIELD=VALUE        Repeat for multiple custom fields. FIELD may be a
                           numeric custom field id (e.g. customfield_210565) or
                           a custom field name (e.g. "QA Scenario") — the CLI
                           resolves names to ids via /rest/api/2/field on
                           first use, caching the mapping under
                           ~/.local/share/cubrid-jira/field-map.json.
```

Resolution rules:

1. If `FIELD` matches `^customfield_\d+$`, use it as the field id verbatim.
2. Otherwise, look it up against the cached field name -> id map; refresh from `/rest/api/2/field` on miss.
3. If a name is ambiguous (two custom fields with the same display name), error out and ask the user to disambiguate by id.

Apply the same `--field` semantics to `cubrid-jira update` so post-creation edits use the same surface.

### Why a name lookup, not just an id

Forcing users to know `customfield_210565` is a UX defect — JIRA field ids are opaque numbers that vary between JIRA instances. Letting them write `--field "QA Scenario=Not applicable; analysis ticket"` is the difference between "usable CLI" and "raw curl wrapper".

### Alternative considered — `--qa-scenario` only

Reject. Hard-coding a single CUBRID-specific field commits the tool to the current CBRD project shape forever. If CUBRID adds a second required custom field next quarter, we're back here. Build the general lever once.

---

## Secondary task — verify `--output json` returns the new issue key

The current `--output json` flag implies the success response is machine-readable, but because every create against CBRD currently fails before that path, **we have no evidence the success path actually emits the new issue key**.

Verify that on a successful create, `--output json` prints a single-line JSON object on stdout containing at minimum:

```json
{"key":"CBRD-NNNNN","self":"http://jira.cubrid.org/rest/api/2/issue/NNNNN","id":"NNNNN"}
```

If it doesn't, fix that too — agents and scripts chaining off `cubrid-jira create` need the new key to do anything useful (rename local draft files, link follow-up issues, post a Slack message, etc.).

Acceptance check:

```bash
NEW_KEY=$(cubrid-jira create --project CBRD --type "..." --summary "..." \
            --description-file ./draft.md \
            --field "QA Scenario=..." \
            --output json --yes | jq -r .key)
[[ "$NEW_KEY" =~ ^CBRD-[0-9]+$ ]] || exit 1
```

---

## Acceptance criteria

- [ ] `cubrid-jira create --field "QA Scenario=<text>" ...` succeeds against `CBRD` and returns HTTP 200/201.
- [ ] `cubrid-jira create --field customfield_210565=<text> ...` also works (raw id path).
- [ ] `cubrid-jira create --field` is repeatable (`--field A=1 --field B=2` sets both).
- [ ] Same `--field` flag works on `cubrid-jira update`.
- [ ] `--output json` on a successful `create` prints a single-line JSON with at least a `key` property matching `^CBRD-\d+$`.
- [ ] Field-name → id mapping is cached on disk; subsequent invocations don't re-hit `/rest/api/2/field`.
- [ ] Ambiguous field names (same display name, different ids) produce a clear error, not a silent wrong-field write.
- [ ] `--dry-run` shows the resolved custom fields in the request body so users can verify before adding `--yes`.
- [ ] `cubrid-jira create -h` documents `--field` with at least one example.

---

## Non-goals

- Don't add a dedicated `--qa-scenario` shortcut. General `--field` is the contract.
- Don't auto-fill required custom fields with placeholders — silently inventing values for "QA Scenario" or similar would corrupt the project's triage workflow. If a required field is missing, surface the JIRA error to the user and tell them which `--field` to add.
- Don't change the dry-run-by-default safety pattern.

---

## Reference

- Existing CLI source: `cubrid-jira` (uv-installed, currently at `~/.local/share/uv/tools/cubrid-jira/bin/cubrid-jira`).
- JIRA REST: `POST /rest/api/2/issue` accepts any `fields.customfield_NNN` key alongside the standard fields.
- JIRA field discovery: `GET /rest/api/2/field` returns the full field list with `id`, `name`, and `custom` properties.
- Project repo: `github.com/vimkim/cubrid-jira`.

---

## Resolution notes (2026-05-22)

Shipped. Live smoke test created **CBRD-26836** end-to-end through the new flag. Acceptance criteria all green except for one wording deviation documented below.

### What was built

- **`--field FIELD=VALUE`** on `create` and `update` — `FIELD` may be a raw `customfield_NNN` id or a display name; names resolve via `/rest/api/2/field` and cache to `<cache_dir>/field-map.json`. Repeatable. Ambiguous names error with both ids surfaced.
- **`VALUE` auto-JSON-decodes** when it starts with `{` or `[`. This was not in the original spec but turned out to be required (see deviation below). Bare strings pass through unchanged.
- **`create --output json`** now emits `{key, id, self, url}` — the doc's "at minimum: key, self, id" requirement plus the existing `url` for convenience.
- New module `src/cubrid_jira/fields.py` (pure parser + on-disk JSON cache; layering-tested to forbid urllib).
- Field-map cache co-located with the issues cache so `$CUBRID_JIRA_DIR` isolation in tests covers it for free.

### Deviation from spec: `QA Scenario` is a single-select, not text

The spec's example `--field "QA Scenario=<text>"` assumed the field accepted free text. In reality `customfield_210565` on `jira.cubrid.org` has schema:

```json
{"type": "option",
 "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select",
 "allowedValues": ["Not Required","Required","Revise Required","Not Yet","In Progress","Complete"]}
```

A bare string write fails with `HTTP 400: Could not find valid 'id' or 'value' in the Parent Option object`. The correct shape is JSON:

```sh
cubrid-jira create --project CBRD --type Task --summary "..." \
  --field 'QA Scenario={"value":"Not Required"}' --yes
```

This is the right answer for all select / cascading-select / multi-select / user / date / version custom fields — JIRA expects an object, not a string. The `--field` flag handles all of them uniformly via the JSON-decode path; the user owns the value shape, the CLI owns the transport.

### How to discover allowed values for any required custom field

```sh
curl -u "$CUBRID_JIRA_USER:$CUBRID_JIRA_PASSWORD" \
  'http://jira.cubrid.org/rest/api/2/issue/createmeta?projectKeys=<PROJ>&issuetypeNames=<TYPE>&expand=projects.issuetypes.fields' \
  | jq '.projects[0].issuetypes[0].fields["customfield_NNN"]'
```

### Acceptance criteria — final state

- [x] `--field "QA Scenario={"value":"..."}" ...` succeeds against CBRD and returns 201. (Wording amended: select-field needs object value, not bare text.)
- [x] `--field customfield_210565={"value":"..."} ...` also works (raw id path; no `/rest/api/2/field` GET).
- [x] `--field` is repeatable.
- [x] Same `--field` flag works on `cubrid-jira update`.
- [x] `--output json` on successful `create` prints `{key, id, self, url}` on stdout, all one line.
- [x] Field-name → id map cached on disk; subsequent invocations skip the GET.
- [x] Ambiguous display names error out and name both candidate ids.
- [x] `--dry-run` shows the resolved customfield id and decoded value in the request body.
- [x] `cubrid-jira create -h` documents `--field` with an example.
