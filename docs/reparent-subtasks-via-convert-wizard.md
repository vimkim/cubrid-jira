# Reparenting CUBRID JIRA sub-tasks via the Convert wizard

> Field report from 2026-05-22. Tested against JIRA Server **7.7.1#77002** at
> `jira.cubrid.org`. Verified by reparenting 9 sub-tasks of the OOS M2 EPIC
> (CBRD-26583) to CBRD-26835.
>
> **Shipped as CLI**: the recipe in this document is implemented as the
> `cubrid-jira convert-to-issue`, `cubrid-jira convert-to-subtask`, and
> `cubrid-jira reparent` subcommands — see the **Reparent / Convert** section
> in the [project README](../README.md#reparent--convert) for the user-facing
> interface, dry-run semantics, and atomicity behavior.

This document explains how to change a Sub-task's parent on JIRA Server when
the REST API silently refuses to do it, by driving the same HTML Convert
wizard the web UI uses.

It exists because the obvious approach — `PUT /rest/api/2/issue/{KEY}` with
`{"fields":{"parent":{"key":"NEW"}}}` — **returns HTTP 204 but does not
mutate the parent field** on this server. We burned a couple of hours
diagnosing that; this is the recipe that actually works.

---

## Why the REST PUT silently no-ops

JIRA Server has a per-project **Field Configuration Scheme** that controls
which fields appear on each issue type's **Create / Edit / View screens**.
For the CBRD project's Sub-task issuetype, `parent` is **not** on the Edit
screen.

When that's true, JIRA's REST behavior is treacherous:

| Payload variant | Response | Actually mutates? |
|---|---|---|
| `{"fields":{"parent":{"key":"X"}}}` | **204 No Content** | **No.** Field silently stripped. |
| `{"fields":{"parent":null}}` | 400 `"data was not an object"` | No |
| `{"fields":{"parent":{}}}` | 400 `"Issue type 10500 is not a sub-task but a parent is specified."` | No |
| `{"update":{"parent":[{"set":{"key":"X"}}]}}` | 400 `"Field 'parent' cannot be set. It is not on the appropriate screen, or unknown."` | No |
| `{"fields":{"issuetype":{"name":"Task"}}}` | 400 same as above | No |

The 204 path is the dangerous one — it lies. Always verify with a follow-up
`GET /rest/api/2/issue/{KEY}?fields=parent` to confirm the change landed.

### Why JIRA does this

Changing a sub-task's parent isn't an attribute edit — it's a **structural
workflow operation**. The web UI exposes it as **Convert to Issue → Convert
to Sub-Task**, a 4-step wizard that runs index updates, link cleanup, and
workflow consistency checks that a raw field PUT skips. The REST `update`
path is gated on the Edit-screen configuration; the Convert path bypasses it.

### Structural fixes (if you can get them)

If you have project-admin or global-admin access, you can avoid this entire
song-and-dance by:

1. Adding the `parent` field to the Edit screen for the Sub-task issuetype in
   the project's Screen Scheme. Once that lands, the plain
   `PUT /issue/{KEY}` with `{"fields":{"parent":{"key":"X"}}}` works.
2. Or granting the user the **MOVE_ISSUE** project permission, which is what
   gates the JIRA Server "Move Issue" REST path.

Without those, the rest of this document applies.

---

## The endpoint map

The Convert wizard is **two** wizards, each 4 steps:

### Forward — Sub-task → Task (clear parent)

| Step | Verb | URL | Notable params |
|---|---|---|---|
| Page  | GET  | `/secure/ConvertSubTask.jspa?id={issueId}` | — |
| 1     | POST | `/secure/ConvertSubTaskSetIssueType.jspa` | `issuetype={taskId}` |
| 2     | —    | *(skipped — Task has no parent)* | — |
| 3     | POST | `/secure/ConvertSubTaskUpdateFields.jspa` | pass-through |
| 4     | POST | `/secure/ConvertSubTaskConvert.jspa`     | `Finish=Finish`, **with `X-Atlassian-Token: no-check` header** |

### Reverse — anything → Sub-task with new parent

| Step | Verb | URL | Notable params |
|---|---|---|---|
| Page  | GET  | `/secure/ConvertIssue.jspa?id={issueId}` | — |
| 1     | POST | `/secure/ConvertIssueSetIssueType.jspa` | `issuetype={subtaskId}`, `parentIssueKey={NEW_PARENT}` |
| 2     | —    | *(skipped)*  | — |
| 3     | POST | `/secure/ConvertIssueUpdateFields.jspa` | pass-through |
| 4     | POST | `/secure/ConvertIssueConvert.jspa`      | `Finish=Finish`, **with `X-Atlassian-Token: no-check` header** |

**Every** POST carries `id`, `guid`, `atl_token`. The "Next >>" steps use
`Next >>=Next >>` as a synthetic submit; the final commit uses `Finish=Finish`.

The numeric issuetype IDs on this server:

- **Sub-task** = `5`
- **Task** = `10500`

These are **per-instance** — check `editmeta` or the wizard's issuetype `<select>` on
your server before reusing.

---

## The traps (in the order I hit them)

### Trap 1: Wrong URL

The non-`!default` variant exists; the `!default` variant doesn't.

- ❌ `/secure/ConvertSubTaskToIssue!default.jspa` → 404 "dead link"
- ✅ `/secure/ConvertSubTask.jspa?id={id}`

Find the real URL by GETing `/browse/{KEY}` (with session) and grepping for
`Convert` in the rendered menu HTML.

### Trap 2: Anonymous reads, anonymous writes

`jira.cubrid.org` allows anonymous reads of CBRD. So `curl --netrc` and
`curl -u …` look identical for GETs even when creds aren't reaching the
server. The PUT then returns a *misleading* 400 — "You do not have
permission to edit issues in this project" — instead of a clean 401.

**Diagnostic**: hit `/rest/api/2/myself`. If you get 401, you're anonymous,
no matter what your other calls said.

### Trap 3: Basic auth doesn't carry into the wizard

The wizard endpoints (`/secure/*.jspa`) require a **session cookie**
(`JSESSIONID`), not basic auth. Two-step:

1. POST `/rest/auth/1/session` with `{"username","password"}` → server
   returns JSESSIONID in the body.
2. GET any page (e.g. `/secure/Dashboard.jspa`) to "warm" the session —
   that's when JIRA actually sets the cookie via `Set-Cookie`.

In Python, `http.cookiejar.CookieJar` + `urllib.request.HTTPCookieProcessor`
handles this transparently. With raw curl, use `-c jar -b jar`.

### Trap 4: XSRF Security Token Missing

The wizard intermediate POSTs (Steps 1, 3) pass without an `X-Atlassian-Token`
header. The **commit** POST (Step 4, `*Convert.jspa`) does not — it returns
HTML titled `XSRF Security Token Missing` even when `atl_token` matches the
cookie exactly. The fix is **add `X-Atlassian-Token: no-check`** to every
mutating POST, just in case.

This is the documented escape hatch for non-browser clients; JIRA treats
its presence as "the caller has thought about XSRF; trust them."

### Trap 5: `atl_token` re-extraction

The cookie value (`atlassian.xsrf.token`) has the form
`<tab-id>|<token>|<source>` — three pipe-separated parts. The form's hidden
field has the same value. A naïve regex like `value="([^"]+)"` captures all
three parts; *don't* trim the `|lin` suffix manually.

In practice on this server the token value didn't change across wizard
steps, but **re-extract from each step's HTML anyway** — JIRA reserves the
right to rotate it on response, and a stale token gives the same
"XSRF Security Token Missing" page as a missing one.

### Trap 6: The 204-lying PUT (already covered above)

Always read back. If `PUT /issue/{KEY}` returns 204, the *next* call must be
`GET /issue/{KEY}?fields=parent,issuetype` to confirm the mutation.

### Trap 7: JQL is index-lagged

`/rest/api/2/search?jql=parent=NEW_PARENT` reads Lucene; it can take seconds
(occasionally longer) to catch up after a write. Per-issue GETs read the
database directly and are authoritative for verification.

### Trap 8: Safety check before the forward step

The forward conversion turns a Sub-task into a Task and **drops the parent**.
If a script blindly runs the forward step on an issue that's *already* a
non-subtask (because of a half-finished prior run, or manual intervention),
it will reject the request — but in any case, the script should refuse to
proceed and skip straight to the reverse step. See the reference script
below for the precondition check.

---

## Reference Python driver

The exact script that did this run lives next to this doc as
[`reparent_via_wizard.py`](./reparent_via_wizard.py). The key flow is
reproduced inline below.

Key invariants:

- **Halt-on-first-error.** If any issue fails mid-conversion, leaving it as a
  Task with no parent is a worse state than the starting Sub-task. Halting
  forces a human to triage rather than continuing to mangle more issues.
- **Verify after each conversion direction.** After Step 4 of the forward
  wizard, GET the issue and assert `type=Task, parent=(none)`. After Step 4
  of the reverse wizard, assert `type=Sub-task, parent=NEW_PARENT`. The
  wizard returns HTTP 200 for the *render of the success page* even when
  the commit silently failed XSRF; the only reliable signal is a follow-up
  GET.
- **Re-fetch atl_token / guid from each step's HTML.** Don't reuse Step 0's
  values for Step 4.

```python
# (see /tmp/jira_reparent.py for the full source)

BASE = "http://jira.cubrid.org"
TASK_ISSUETYPE_ID = "10500"
SUBTASK_ISSUETYPE_ID = "5"

def convert_subtask_to_task(opener, issue_id, key):
    html = html_get(opener, f"/secure/ConvertSubTask.jspa?id={issue_id}")
    form = parse_form(html)  # extracts atl_token, guid

    # Step 1
    _, html = html_post(opener, "/secure/ConvertSubTaskSetIssueType.jspa", {
        "id": issue_id, "guid": form["guid"],
        "atl_token": form["atl_token"],
        "issuetype": TASK_ISSUETYPE_ID,
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)

    # Step 3 (Step 2 skipped for Task target)
    _, html = html_post(opener, "/secure/ConvertSubTaskUpdateFields.jspa", {
        "id": issue_id, "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)

    # Step 4 — Finish
    _, html = html_post(opener, "/secure/ConvertSubTaskConvert.jspa", {
        "id": issue_id, "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Finish": "Finish",
    })
    check_xsrf(html)

def convert_task_to_subtask(opener, issue_id, key, parent_key):
    html = html_get(opener, f"/secure/ConvertIssue.jspa?id={issue_id}")
    form = parse_form(html)

    # Step 1 — issuetype AND parentIssueKey
    _, html = html_post(opener, "/secure/ConvertIssueSetIssueType.jspa", {
        "id": issue_id, "guid": form["guid"],
        "atl_token": form["atl_token"],
        "issuetype": SUBTASK_ISSUETYPE_ID,
        "parentIssueKey": parent_key,
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)

    _, html = html_post(opener, "/secure/ConvertIssueUpdateFields.jspa", {
        "id": issue_id, "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Next >>": "Next >>",
    })
    check_xsrf(html)
    form = parse_form(html)

    _, html = html_post(opener, "/secure/ConvertIssueConvert.jspa", {
        "id": issue_id, "guid": form["guid"],
        "atl_token": form["atl_token"],
        "Finish": "Finish",
    })
    check_xsrf(html)
```

`html_post` sets two request headers on every POST:

```python
req.add_header("Content-Type", "application/x-www-form-urlencoded")
req.add_header("X-Atlassian-Token", "no-check")
```

`check_xsrf(html)` raises if the response body contains
`"XSRF Security Token Missing"` — JIRA returns 200 with that page even when
the commit was rejected.

---

## Curl-only smoke test

For a one-off (or to validate the recipe on a new JIRA Server before
committing to a script), this is a complete forward conversion of one
sub-task to a Task in plain bash:

```sh
COOKIE_JAR=$(mktemp)
ID=1418856   # CBRD-26828's numeric id; from `GET /rest/api/2/issue/CBRD-26828`

# Warm the session.
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -L \
  "$BASE/secure/Dashboard.jspa" >/dev/null

# Authenticate.
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"$CUBRID_JIRA_USER\",\"password\":\"$CUBRID_JIRA_PASSWORD\"}" \
  "$BASE/rest/auth/1/session" >/dev/null

# Wizard page → extract atl_token + guid.
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -L \
  "$BASE/secure/ConvertSubTask.jspa?id=$ID" > /tmp/cs.html
ATL=$(python3 -c 'import re,sys; print(re.search(r"name=\"atl_token\"\s+value=\"([^\"]+)\"", open("/tmp/cs.html").read()).group(1))')
GUID=$(python3 -c 'import re,sys; print(re.search(r"name=\"guid\"\s+value=\"([^\"]+)\"', open("/tmp/cs.html").read()).group(1))')

# Step 1 → 3 → 4.
for path in ConvertSubTaskSetIssueType ConvertSubTaskUpdateFields ConvertSubTaskConvert ; do
  SUBMIT="Next >>" ; [ "$path" = "ConvertSubTaskConvert" ] && SUBMIT=Finish
  EXTRA=() ; [ "$path" = "ConvertSubTaskSetIssueType" ] && EXTRA=(--data-urlencode "issuetype=10500")
  curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -L \
    -X POST -H 'X-Atlassian-Token: no-check' \
    --data-urlencode "id=$ID" --data-urlencode "guid=$GUID" \
    --data-urlencode "atl_token=$ATL" --data-urlencode "$SUBMIT=$SUBMIT" \
    "${EXTRA[@]}" "$BASE/secure/$path.jspa" > /tmp/step.html
  # re-extract atl_token / guid for the next step
  ATL=$(python3 -c 'import re; m=re.search(r"name=\"atl_token\"\s+value=\"([^\"]+)\"", open("/tmp/step.html").read()); print(m.group(1) if m else "")')
done

# Verify.
curl -sS -u "$CUBRID_JIRA_USER:$CUBRID_JIRA_PASSWORD" \
  "$BASE/rest/api/2/issue/CBRD-26828?fields=issuetype,parent" \
  | python3 -m json.tool | grep -E '"name"|"key"'
```

Expected after: `"issuetype": "Task"`, no `parent` field.

---

## Suggestion: a `cubrid-jira reparent` subcommand

This script is shaped exactly like the project's existing write subcommands
(dry-run by default, `--yes` to commit, cache invalidation on success). It's
a natural sixth subcommand alongside `create`, `comment`, `link`,
`transition`, `assign`:

```sh
cubrid-jira reparent CBRD-26660 --to CBRD-26835            # dry-run
cubrid-jira reparent CBRD-26660 --to CBRD-26835 --yes      # commit
```

The implementation differs from the others in two ways:

1. It needs a session-cookie HTTP client, not the existing basic-auth
   one in `client.py`. A `cubrid_jira_fetcher/session.py` module that
   wraps `urllib.request` with a `CookieJar` would slot in cleanly.
2. It must invalidate **three** cache entries on success: the moved
   issue, the old parent, and the new parent. The existing
   `cache.invalidate(KEY, cache_dir)` helper handles one at a time —
   call it three times.

If you take this on, the test fixture pattern in `tests/` can mock the
two wizard URLs without needing a live server.
