# CLAUDE.md — agent contract for cubrid-jira

```text
canonical binary    cubrid-jira <subcommand> [args…]
subcommands         search | create | comment | link | transition | assign
credentials         env CUBRID_JIRA_USER + CUBRID_JIRA_PASSWORD
                    (no interactive prompt; falls back to ~/.netrc)
cache directory     $CUBRID_JIRA_DIR  ||  ~/.local/share/cubrid-jira/issues/
output (stdout)     markdown or JSON
output (stderr)     status + errors
machine-readable    add `--output json` to write subcommands
                    → exactly one JSON object on stdout
exit codes          0 ok | 1 generic | 2 401 | 3 403 | 4 404 | 5 400
```

## CRITICAL write-safety rules

- **All writes are dry-run by default.** `--yes` is required to actually send. Treat the absence of `--yes` as a no-op.
- **On HTTP 401, never retry.** Jira Server locks accounts and triggers a CAPTCHA after a few failed basic-auth attempts. The client exits 2 immediately; any retry loop in user code is a footgun. Reset the CAPTCHA via the web UI before re-running.

## Module layout (`src/cubrid_jira/`)

```text
cli.py        parent argparse + dispatch + payload builders
http.py       JiraClient (auth, dry-run, retries) + fetch_issue (unauth GET)
markdown.py   Jira-wiki → markdown rendering; pure
walk.py       recursive related-issue traversal + save_issue cache write
auth.py       env → netrc → error credential resolution
cache.py      cache dir resolve + prefix-safe invalidation
legacy.py     deprecation shims for cubrid-jira-search / cubrid-jira-fetch
```

Layering invariants (enforced by `tests/test_layering.py`):

- `markdown.py` does **not** import `urllib` — rendering is pure.
- `http.py` does **not** import `subprocess` — no pandoc/process spawning here.
- `walk.py` is the only module allowed to mix the HTTP and markdown layers.

The legacy import path `cubrid_jira_fetcher` re-exports `cubrid_jira` and emits a `DeprecationWarning`; do not write new code against it.

## Tests

```sh
uv run pytest              # unit + mocked integration (~0.2s)
uv run pytest -m live      # also hits the real jira.cubrid.org (read-only)
```
