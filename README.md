# cubrid-jira

A CUBRID JIRA CLI for `http://jira.cubrid.org` with three workflow buckets:

* **cache-first reads** (`search`) — markdown to stdout, no network on a cache hit.
* **field writes** (`create`, `comment`, `link`, `transition`, `assign`) — dry-run by default; `--yes` to send.
* **structural writes** (`convert-to-issue`, `convert-to-subtask`, `reparent`) — drive the JIRA Convert wizard for the operations REST silently no-ops on; same dry-run contract.

Designed to be driven by AI agents, slash commands, and shell pipelines.

---

## For AI agents — 30-second contract

If you are an autonomous agent running in a shell, this is everything you need:

```text
Canonical command   : cubrid-jira <subcommand> [args…]
Subcommands         : read              search
                      field-write       create | comment | link | transition | assign | update
                      structural-write  convert-to-issue | convert-to-subtask | reparent
Credentials         : env  CUBRID_JIRA_USER  +  CUBRID_JIRA_PASSWORD
                      (no interactive prompt; falls back to ~/.netrc)
Output contract     : markdown / JSON   → stdout
                      status / progress → stderr
                      (safe to pipe stdout)
Machine-readable    : add `--output json` to any write subcommand;
                      stdout becomes exactly one JSON object.
Dry-run is default  : ALL writes are dry-run unless you pass `--yes`.
                      This includes the structural writes.
CAPTCHA lockout     : on HTTP 401 the tool exits 2 immediately and does
                      NOT retry. Jira Server locks the account and
                      forces a web-UI CAPTCHA after repeated failures.
Exit codes          : 0 ok | 1 generic | 2 401 | 3 403 | 4 404 | 5 400
                      (see "Error contract" below for what they mean)
```

`cubrid-jira search CBRD-XXXXX` is the agent-friendly read; use it freely.
Any write subcommand without `--yes` is **safe to invoke** — it only prints the planned request.

---

![Demo](./demo.gif)

> The demo gif shows the **read-only** flow (`cubrid-jira search`). The write subcommands shipped after the demo was recorded.

---

## Install

`uv tool install` is the right tool: isolated env, binary on `$PATH`, easy uninstall. `pipx` is an equivalent fallback.

```sh
# Recommended:
uv tool install git+https://github.com/vimkim/cubrid-jira.git

# pipx (equivalent, slower):
pipx install git+https://github.com/vimkim/cubrid-jira.git

# From a local clone:
cd cubrid-jira && uv tool install . || pipx install .
```

Installs three binaries on `$PATH`:

| Binary | Status |
|---|---|
| `cubrid-jira` | **canonical** — use this |
| `cubrid-jira-search` | deprecated alias for `cubrid-jira search` |
| `cubrid-jira-fetch` | deprecated bulk-fetch tool — the original CLI before the v1.0 rename |

> Do **not** use `pip install -e .` to install — that mode is only for editing this repo's source.

## Prerequisites

- **Python 3.14+**
- **[pandoc](https://pandoc.org/)** — converts Jira wiki markup to markdown

```sh
brew install pandoc          # macOS / Linuxbrew
sudo apt install pandoc      # Debian / Ubuntu
sudo dnf install pandoc      # Fedora / RHEL
```

Optional: [`uv`](https://github.com/astral-sh/uv), [`just`](https://github.com/casey/just).

---

## Read flow — `cubrid-jira search`

Prints one issue's markdown to **stdout**; progress goes to stderr, so piping stays clean.

```sh
cubrid-jira search CBRD-26463
cubrid-jira search http://jira.cubrid.org/browse/CBRD-26463
cubrid-jira search CBRD-26463 --force         # bypass cache
cubrid-jira search CBRD-26463 --no-recurse    # don't walk related on miss
cubrid-jira search CBRD-26463 --dir /tmp/jira # override cache directory
```

How it works:

1. Look for `CBRD-26463*.md` in the cache directory.
2. **Cache hit** → print it. No network.
3. **Cache miss** → fetch the issue (+ 1 level of related issues) into the cache, then print.
4. Exit non-zero on fetch failure.

### Cache directory

Resolved in order (first match wins):

1. `--dir DIR`
2. `$CUBRID_JIRA_DIR`
3. `~/.local/share/cubrid-jira/issues/` (default)

Recommended one-time setup:

```sh
echo 'export CUBRID_JIRA_DIR="$HOME/.local/share/cubrid-jira/issues"' >> ~/.bashrc
```

---

## Field-write flow — `create / comment / link / transition / assign / update`

These edit fields on an existing or new issue. Same dry-run-by-default contract as the structural writes ([next section](#structural-write-flow--convert--reparent)); pass `--yes` to actually send.

```sh
# Read first — review the planned request:
cubrid-jira create --project CBRD --type Bug --summary "..."
# Then commit with --yes:
cubrid-jira create --project CBRD --type Bug --summary "..." --yes
```

### Subcommand reference

```sh
cubrid-jira create     --project CBRD --type Bug --summary "..." \
                       [--description-file path] [--priority Major] [--assignee user] \
                       [--label l1 --label l2] [--component sql] \
                       [--link-relates CBRD-Y] [--link-blocks CBRD-Z]
cubrid-jira comment    CBRD-XXXXX --body-file note.md
cubrid-jira link       CBRD-A --type Relates --to CBRD-B   # also Blocks | Cloners | Duplicate
cubrid-jira transition CBRD-A [--to "In Progress"]         # omit --to to list available
cubrid-jira assign     CBRD-A --to <username>              # --to "" to unassign
cubrid-jira update     CBRD-A [--summary "..."] [--description-file path] \
                       [--priority Major] [--label l1 --label l2] [--component sql]
```

`update` edits an existing issue's fields. At least one of `--summary`, `--description-file`, `--priority`, `--label`, or `--component` is required. **`--label` and `--component` replace the full list** — they are not additive (Jira REST `fields` semantics). `--description-file -` reads from stdin.

Global flags on every write subcommand:

| Flag | Default | Description |
|---|---|---|
| `--dry-run` | (always on unless `--yes`) | Print the resolved URL, masked headers, and JSON body. Don't send. |
| `--yes` | off | Required to actually perform the live write. |
| `--server URL` | `http://jira.cubrid.org` | JIRA base URL. |
| `-d`, `--dir DIR` | shared cache | Cache directory for post-write cache updates. |
| `--output {text,json}` | `text` | Machine-readable output mode; see below. |

### Cache interaction on writes

- `create` (live): the new issue is fetched and saved into the cache, so `cubrid-jira search NEW-KEY` is an immediate hit.
- `comment`, `link`, `transition`, `assign`, `update` (live): cached markdown for the affected issue key(s) is **deleted**, so the next read re-fetches. `link` invalidates both sides.

---

## Structural-write flow — `convert-to-issue / convert-to-subtask / reparent`

Three subcommands change the **structural type** of an issue rather than editing its fields:

```sh
cubrid-jira convert-to-issue   CBRD-XXXXX [--type Task]                 # Sub-task → Task (drops parent)
cubrid-jira convert-to-subtask CBRD-XXXXX --to CBRD-YYYYY               # Task → Sub-task of YYYYY
cubrid-jira reparent           CBRD-XXXXX --to CBRD-YYYYY               # move a Sub-task under YYYYY
```

`reparent` composes the other two for the common case of changing a sub-task's parent.

### Why these aren't just a REST `PUT`

On `jira.cubrid.org` (JIRA Server 7.7.1), `PUT /rest/api/2/issue/{KEY}` with `{"fields":{"parent":{"key":"X"}}}` **returns HTTP 204 but does not mutate the parent field** — the CBRD project's Field Configuration Scheme doesn't put `parent` on the Sub-task Edit screen, so the API silently strips it. These three subcommands drive the same **Convert wizard** the web UI uses (`/secure/ConvertSubTask.jspa` and `/secure/ConvertIssue.jspa`) via session cookies and form POSTs, including the `X-Atlassian-Token: no-check` header required to bypass JIRA's XSRF gate on non-browser clients.

Full technical rationale, the trap list (8 of them), and a curl-only smoke test recipe live in [`docs/reparent-subtasks-via-convert-wizard.md`](./docs/reparent-subtasks-via-convert-wizard.md).

### What they do

| Subcommand | Pre-flight | Cache invalidates | Notes |
|---|---|---|---|
| `convert-to-issue` | refuses if not Sub-task | issue + previous parent | Drops the parent; default `--type Task`. |
| `convert-to-subtask` | refuses if already Sub-task, or if `--to` is itself a Sub-task | issue + new parent | Default `--type Sub-task`. |
| `reparent` | refuses if not Sub-task, if `--to` equals current parent, or if `--to` is a Sub-task | issue + old parent + new parent | Two-phase: Sub-task → Task, then Task → Sub-task. |

### Atomicity on `reparent`

`reparent` runs the forward wizard (Sub-task → Task), verifies the intermediate state, **then** runs the reverse wizard (Task → Sub-task under `--to`). If the reverse half fails after the forward half succeeded, the issue is left as a Task with no parent — a worse state than it started in. The command does **not** swallow this:

* Prints a loud `!!! ATOMICITY WARNING` to stderr with the exact recovery command (`cubrid-jira convert-to-subtask KEY --to NEW --yes`).
* Exits non-zero (1).
* Does **not** invalidate the cache, so the next `search` re-fetches and surfaces the actual state.

### Issuetype IDs are resolved at runtime

Numeric issuetype IDs (Sub-task, Task, Bug, …) vary per JIRA install. None of the three subcommands hard-codes them; each parses the `<select name="issuetype">` block from the wizard page on every live run and matches by display name. Pointing `--server` at a different JIRA Server picks up that server's IDs automatically.

### Dry-run safety

Like every other write subcommand, all three default to **dry-run**. Without `--yes` they:

1. Fetch the issue's current metadata over the same unauthenticated REST endpoint `search` uses.
2. Run the pre-flight refusals.
3. Print the planned wizard POSTs with `atl_token=<extracted-at-runtime>`, `guid=<extracted-at-runtime>`, and `issuetype=<resolved-at-runtime>` placeholders.
4. Never log in, never contact the wizard endpoints, never touch credentials.

So `cubrid-jira reparent CBRD-1 --to CBRD-2` is safe to run as a preview any time, even without creds set.

---

## Error contract

Applies to every subcommand — read, field-write, and structural-write alike. 401 in particular can fire on a wizard step, not just on the field-write POST.

| Exit | Cause |
|---|---|
| 0 | Success (or dry-run completed). |
| 1 | Generic error: parse failure, network exhaustion, unknown link type, atomicity rollback warning on `reparent`, … |
| 2 | **HTTP 401 — auth failed.** Hard exit, no retry. CAPTCHA-lockout warning printed; the wizard's session login surfaces 401 the same way. |
| 3 | HTTP 403 — authenticated but missing permission. |
| 4 | HTTP 404 — issue key not found. |
| 5 | HTTP 400 — validation; server's `errors` / `errorMessages` payload printed verbatim. |

5xx and transient network errors get one short retry with backoff, then exit 1. Wizard XSRF rejections (atl_token went stale mid-flow) exit 1 with a message naming the failing step.

---

## Output formats

Every **write** subcommand supports `--output {text,json}`.

### `text` (default)

Human-readable status to stderr; the JSON request body (in dry-run) goes to stdout. Suitable for piping to a TTY or to a log.

### `json`

Exactly **one** JSON object on stdout, nothing else. Status/errors still go to stderr. Suitable for `jq`, agent pipelines, and CI gates.

| Subcommand | Live success shape | Dry-run shape |
|---|---|---|
| `create` | `{"key": "CBRD-9999", "url": "..."}` | `{"dry_run": true, "requests": [POST issue, POST issueLink, …]}` |
| `comment` | `{"issue": "CBRD-1", "comment_id": "42"}` | `{"dry_run": true, "requests": [POST .../comment]}` |
| `link` | `{"inward": "CBRD-1", "outward": "CBRD-2", "type": "Relates"}` | `{"dry_run": true, "requests": [POST issueLink]}` |
| `transition` (with `--to`) | `{"issue": "CBRD-1", "transition_id": "21", "to": "In Progress"}` | `{"dry_run": true, "requests": [POST transitions]}` |
| `transition` (list mode) | `{"issue": "CBRD-1", "transitions": [...]}` | (same — listing is a GET) |
| `assign` (set) | `{"issue": "CBRD-1", "assignee": "vimkim"}` | `{"dry_run": true, "requests": [PUT assignee]}` |
| `assign` (clear) | `{"issue": "CBRD-1", "assignee": null}` | (same) |
| `convert-to-issue` | `{"issue": "CBRD-1", "type": "Task", "previous_parent": "CBRD-X"}` | `{"dry_run": true, "requests": [POST step1, step3, step4]}` |
| `convert-to-subtask` | `{"issue": "CBRD-1", "parent": "CBRD-Y", "type": "Sub-task"}` | `{"dry_run": true, "requests": [POST step1, step3, step4]}` |
| `reparent` | `{"issue": "CBRD-1", "from_parent": "CBRD-X", "to_parent": "CBRD-Y"}` | `{"dry_run": true, "requests": [6 POSTs: forward + reverse]}` |

The dry-run `requests` field captures **every** mutation the live run would send (so `create --link-relates X --link-blocks Y` returns the 3-request plan), with `method`, `url`, and `body` per request.

---

## Credentials

### ⚠️ Cleartext + CAPTCHA-lockout warnings

- **Cleartext.** `jira.cubrid.org` is HTTP-only; basic-auth headers travel unencrypted. Use a trusted network and a JIRA-only password.
- **CAPTCHA.** Jira Server 7.7.1 locks an account and forces a web-UI CAPTCHA after a small number of failed basic-auth attempts. The CLI **never retries on 401** to avoid triggering this. If you see `Error: Auth failed (HTTP 401)`, log into `http://jira.cubrid.org` in a browser, solve the CAPTCHA, fix your credentials, and try again.

### Resolution order

1. `CUBRID_JIRA_USER` + `CUBRID_JIRA_PASSWORD` env vars (preferred for agents).
2. `~/.netrc` entry for `jira.cubrid.org`.
3. Hard error with an instructive message — no interactive prompt.

Example `~/.netrc`:

```
machine jira.cubrid.org
  login your-jira-username
  password your-jira-password
```

```sh
chmod 600 ~/.netrc
```

---

## Worked examples

### Create a bug related to `CBRD-26517`

```sh
# 1) Dry-run JSON plan — review what would be sent.
cubrid-jira create \
    --project CBRD \
    --type Bug \
    --summary "OOS: heap_record_replace crashes when …" \
    --priority Major \
    --description-file ./bug-notes.md \
    --link-relates CBRD-26517 \
    --output json
# → {"dry_run": true, "requests": [POST /rest/api/2/issue, POST /rest/api/2/issueLink]}

# 2) Same command with --yes — actually creates the issue + link.
KEY=$(cubrid-jira create \
    --project CBRD \
    --type Bug \
    --summary "OOS: heap_record_replace crashes when …" \
    --priority Major \
    --description-file ./bug-notes.md \
    --link-relates CBRD-26517 \
    --yes --output json | jq -r .key)
echo "Created $KEY"
cubrid-jira search "$KEY"   # immediate cache hit, no extra fetch
```

### Revise the description of an existing issue

```sh
# 1) Dry-run — review the PUT body.
cubrid-jira update CBRD-26517 --description-file ./new-notes.md
# → DRY RUN PUT /rest/api/2/issue/CBRD-26517  {"fields": {"description": "..."}}

# 2) Commit. Cached markdown for CBRD-26517 is deleted, so the next
#    `cubrid-jira search` re-fetches the live issue.
cubrid-jira update CBRD-26517 \
    --summary "OOS: heap_record_replace — updated repro" \
    --description-file ./new-notes.md \
    --yes --output json
# → {"issue": "CBRD-26517", "updated_fields": ["description", "summary"]}
```

### Move a sub-task to a new parent

```sh
# 1) Preview the 6-POST wizard plan (no credentials needed).
cubrid-jira reparent CBRD-26660 --to CBRD-26835 --output json
# → {"dry_run": true, "requests": [...3 forward POSTs..., ...3 reverse POSTs...]}

# 2) Commit. Same call, plus --yes.
cubrid-jira reparent CBRD-26660 --to CBRD-26835 --yes
# Reparented CBRD-26660: CBRD-26583 -> CBRD-26835; cache invalidated for all three keys.
```

If the reverse half fails after the forward half succeeded, `reparent` prints the recovery command and exits 1 — it never silently leaves the issue stranded. See **Atomicity on `reparent`** above.

---

## Caching behavior

The cache directory is shared by `cubrid-jira search` and the legacy `cubrid-jira-fetch` bulk tool. Both honour the same precedence (`--dir`, `$CUBRID_JIRA_DIR`, default). A markdown file written by one is served immediately by the other.

- `cubrid-jira search KEY` — cache hit prints from disk with no network; cache miss fetches one issue plus one level of related issues (`--no-recurse` disables the walk).
- `cubrid-jira-fetch KEY --depth N` *(deprecated)* — bulk-fetch a transitive closure up to depth `N`. Already-saved files are skipped, so a later `--depth 2` run extends a prior `--depth 1` run; pass `--force` to re-download.
- Field-write commands invalidate the affected key(s) (`link` invalidates both sides); structural-write commands invalidate 2–3 keys per the table above.

---

## Development

```sh
git clone https://github.com/vimkim/cubrid-jira.git
cd cubrid-jira
uv sync --dev
uv run pytest                # ~78 unit + mocked-integration tests in ~0.25s
uv run pytest -m live        # also hits the real jira.cubrid.org (read-only)
uv run cubrid-jira search CBRD-1
```

With `just`:

```sh
just test
just search CBRD-26463
```

### Module layout (`src/cubrid_jira/`)

| File | Role |
|---|---|
| `cli.py` | Parent `cubrid-jira` argparse + dispatch. |
| `http.py` | `JiraClient` (basic-auth, dry-run, retries, 401 hard-fail) + `fetch_issue` read helper. **Layering rule: no `subprocess` imports.** |
| `session.py` | `SessionClient` for the Convert wizard — manages `JSESSIONID` via `http.cookiejar.CookieJar` and adds `X-Atlassian-Token: no-check` on every mutating POST. Same dry-run semantics as `JiraClient`. **Layering rule: no `subprocess` imports.** |
| `wizard.py` | Pure HTML parsing (`atl_token` / `guid` / `<select name="issuetype">` extraction, XSRF-rejection detection) + payload builders for the six wizard form POSTs. **Layering rule: no `urllib` imports.** |
| `markdown.py` | Pure rendering (Jira wiki → markdown via pandoc) and `extract_related_keys`. **Layering rule: no `urllib` imports.** |
| `walk.py` | Recursive related-issue walking + on-disk cache writes. |
| `auth.py` | Credential resolution: env → netrc → error. |
| `cache.py` | Cache directory resolution + prefix-safe invalidation. |
| `legacy.py` | Deprecation shims for the old `cubrid-jira-search` / `cubrid-jira-fetch` binaries. |

The `cubrid_jira_fetcher` import path remains as a deprecation shim that re-exports `cubrid_jira` and emits a `DeprecationWarning`. New code should `import cubrid_jira` directly.

---

## Troubleshooting

- **`command not found: cubrid-jira`** — install dir isn't on `$PATH`. With `uv tool install`, run `uv tool update-shell`. With `pipx`, run `pipx ensurepath`. Restart the shell.
- **`pandoc: command not found`** — install pandoc (see Prerequisites). Without pandoc, descriptions/comments fall through as plain text.
- **`Error: Auth failed (HTTP 401)`** — do NOT retry. See [CAPTCHA-lockout warning](#-cleartext--captcha-lockout-warnings). Solve the CAPTCHA via the JIRA web UI, then fix your credentials.
- **Redirect loop / HTTPS errors** — JIRA responses are expected over plain HTTP; do not force HTTPS at the proxy level.
- **Stale cache** — `cubrid-jira search CBRD-XXXXX --force`, or just delete the cache directory.
- **Deprecation warning when importing `cubrid_jira_fetcher`** — expected; rename your import to `cubrid_jira`.
