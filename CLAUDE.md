# CLAUDE.md — agent contract for cubrid-jira

```text
canonical binary    cubrid-jira <subcommand> [args…]
read                search
field-write         create | comment | link | transition | assign
structural-write    convert-to-issue | convert-to-subtask | reparent
credentials         env CUBRID_JIRA_USER + CUBRID_JIRA_PASSWORD
                    (no interactive prompt; falls back to ~/.netrc)
cache directory     $CUBRID_JIRA_DIR  ||  ~/.local/share/cubrid-jira/issues/
output (stdout)     markdown or JSON
output (stderr)     status + errors
machine-readable    add `--output json` to write subcommands
                    → exactly one JSON object on stdout
exit codes          0 ok | 1 generic | 2 401 | 3 403 | 4 404 | 5 400
```

## Structural writes — convert / reparent

`PUT /rest/api/2/issue/{KEY}` with `{"fields":{"parent":{"key":"X"}}}` returns
**204 but silently no-ops** on this server's Field Configuration Scheme. The
three structural-write subcommands drive the Convert wizard instead:

```text
convert-to-issue   <KEY> [--type Task]            Sub-task -> Task (drops parent)
convert-to-subtask <KEY> --to <PARENT>            non-Sub-task -> Sub-task of PARENT
reparent           <KEY> --to <PARENT>            move a Sub-task between parents
```

- Dry-run is the default; `--yes` performs the live wizard sequence.
- `reparent` is atomic-or-loud: if the reverse half fails after the forward
  half succeeded, the issue is left as a Task with no parent — the command
  prints a `!!! ATOMICITY WARNING` with the exact recovery command and
  exits 1. Do **not** silently retry.
- Issuetype IDs (Sub-task / Task) are resolved from the wizard page's
  `<select name="issuetype">` at runtime, not hard-coded.
- Cache invalidation: convert-* invalidates 2 keys (issue + the other parent),
  reparent invalidates 3 (issue + old parent + new parent).

Full background, traps, and curl-only smoke test:
`docs/reparent-subtasks-via-convert-wizard.md`.

## CRITICAL write-safety rules

- **All writes are dry-run by default.** `--yes` is required to actually send. Treat the absence of `--yes` as a no-op.
- **On HTTP 401, never retry.** Jira Server locks accounts and triggers a CAPTCHA after a few failed basic-auth attempts. The client exits 2 immediately; any retry loop in user code is a footgun. Reset the CAPTCHA via the web UI before re-running.

## Module layout (`src/cubrid_jira/`)

```text
cli.py        parent argparse + dispatch + payload builders
http.py       JiraClient (basic-auth, dry-run, retries) + fetch_issue (unauth GET)
session.py    SessionClient — JSESSIONID cookies + X-Atlassian-Token for wizard POSTs
wizard.py     pure HTML parsing + form-payload builders for the Convert wizard
markdown.py   Jira-wiki → markdown rendering; pure
walk.py       recursive related-issue traversal + save_issue cache write
auth.py       env → netrc → error credential resolution
cache.py      cache dir resolve + prefix-safe invalidation
legacy.py     deprecation shims for cubrid-jira-search / cubrid-jira-fetch
```

Layering invariants (enforced by `tests/test_layering.py`):

- `markdown.py` does **not** import `urllib` — rendering is pure.
- `wizard.py`   does **not** import `urllib` — parsing is pure.
- `http.py`    does **not** import `subprocess` — no pandoc/process spawning.
- `session.py` does **not** import `subprocess` — same rule as http.py.
- `walk.py` is the only module allowed to mix the HTTP and markdown layers.

The legacy import path `cubrid_jira_fetcher` re-exports `cubrid_jira` and emits a `DeprecationWarning`; do not write new code against it.

## Tests

```sh
uv run pytest              # unit + mocked integration (~0.2s)
uv run pytest -m live      # also hits the real jira.cubrid.org (read-only)
```
