# cubrid-jira

A CUBRID JIRA CLI: **cache-first reads** and **dry-run-default writes** against `http://jira.cubrid.org`. Designed to be driven by AI agents, slash commands, and shell pipelines.

> Renamed from `cubrid-jira-fetcher` in v1.0. The old `cubrid-jira-search` and `cubrid-jira-fetch` binaries still work but emit a deprecation notice.

---

## For AI agents — 30-second contract

If you are an autonomous agent running in a shell, this is everything you need:

```text
Canonical command   : cubrid-jira <subcommand> [args…]
Subcommands         : search | create | comment | link | transition | assign
                      | convert-to-issue | convert-to-subtask | reparent
Credentials         : env  CUBRID_JIRA_USER  +  CUBRID_JIRA_PASSWORD
                      (no interactive prompt; falls back to ~/.netrc)
Output contract     : markdown / JSON   → stdout
                      status / progress → stderr
                      (safe to pipe stdout)
Machine-readable    : add `--output json` to any write subcommand;
                      stdout becomes exactly one JSON object.
Dry-run is default  : ALL writes are dry-run unless you pass `--yes`.
CAPTCHA lockout     : on HTTP 401 the tool exits 2 immediately and does
                      NOT retry. Jira Server locks the account and
                      forces a web-UI CAPTCHA after repeated failures.
Exit codes          : 0 ok | 1 generic | 2 401 | 3 403 | 4 404 | 5 400
```

`cubrid-jira search CBRD-XXXXX` is the agent-friendly read; use it freely.
Any of the write subcommands without `--yes` is **safe to invoke** — it only prints the planned request.

---

![Demo](./demo.gif)

> The demo gif shows the **read-only** flow (`cubrid-jira search`). The write subcommands shipped after the demo was recorded.

---

## Install

`uv tool install` is the right tool: isolated env, binary on `$PATH`, easy uninstall. `pipx` is an equivalent fallback.

```sh
# Recommended:
uv tool install git+https://github.com/vimkim/cubrid-jira-fetcher.git

# pipx (equivalent, slower):
pipx install git+https://github.com/vimkim/cubrid-jira-fetcher.git

# From a local clone:
cd cubrid-jira-fetcher && uv tool install . || pipx install .
```

Installs three binaries on `$PATH`:

| Binary | Status |
|---|---|
| `cubrid-jira` | **canonical** — use this |
| `cubrid-jira-search` | deprecated alias for `cubrid-jira search` |
| `cubrid-jira-fetch` | deprecated bulk-fetch tool (was `cubrid-jira-fetcher`'s original entry point) |

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

## Write flow — `create / comment / link / transition / assign`

All write subcommands are **dry-run by default**; you must pass `--yes` to actually send.

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
```

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
- `comment`, `link`, `transition`, `assign` (live): cached markdown for the affected issue key(s) is **deleted**, so the next read re-fetches. `link` invalidates both sides.

### Error contract

| Exit | Cause |
|---|---|
| 0 | Success (or dry-run completed). |
| 1 | Generic error: parse failure, network exhaustion, unknown link type. |
| 2 | **HTTP 401 — auth failed.** Hard exit, no retry. CAPTCHA-lockout warning printed. |
| 3 | HTTP 403 — authenticated but missing permission. |
| 4 | HTTP 404 — issue key not found. |
| 5 | HTTP 400 — validation; server's `errors` / `errorMessages` payload printed verbatim. |

5xx and transient network errors get one short retry with backoff, then exit 1.

---

## Reparent / Convert

Three additional subcommands change the **structural type** of an issue rather than editing its fields:

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

The numeric issuetype IDs on this server happen to be `5` (Sub-task) and `10500` (Task) — but those vary per JIRA install. None of the three subcommands hard-codes them; each parses the `<select name="issuetype">` block from the wizard page on every live run and matches by display name. If you point `--server` at a different JIRA Server you get the right IDs automatically.

### Dry-run safety

Like every other write subcommand, all three default to **dry-run**. Without `--yes` they:

1. Fetch the issue's current metadata (unauthenticated read).
2. Run the pre-flight refusals.
3. Print the planned wizard POSTs with `atl_token=<extracted-at-runtime>`, `guid=<extracted-at-runtime>`, and `issuetype=<resolved-at-runtime>` placeholders.
4. Never log in, never contact the wizard endpoints.

So `cubrid-jira reparent CBRD-1 --to CBRD-2` is safe to run as a preview at any time — it touches the same unauthenticated `/rest/api/2/issue/...` endpoint `search` uses.

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

## Worked example — create a bug related to `CBRD-26517`

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

---

## Caching behavior

`cubrid-jira search` and the legacy `cubrid-jira-fetch` bulk tool share the same on-disk cache. Already-saved files are skipped on subsequent runs; related issues are still traversed so a later `--depth 2` run correctly extends a prior `--depth 1` run. Use `--force` to re-download.

A markdown file written by `cubrid-jira-fetch` is served immediately by `cubrid-jira search` (and vice-versa) when both point at the same directory.

---

## Development

```sh
git clone https://github.com/vimkim/cubrid-jira-fetcher.git
cd cubrid-jira-fetcher
uv sync --dev
uv run pytest                # unit + integration tests (47+ tests, ~0.2s)
uv run pytest -m live        # also runs the live-server smoke test
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
