# CLAUDE.md — agent contract for cubrid-jira

```text
canonical binary    cubrid-jira <subcommand> [args…]
read                search (one issue by key, cache-first) | jql (list by query, live)
                    | attachment (list/download issue attachments, live)
field-write         create | comment | comment-list | comment-update | comment-delete | link | transition | assign | update
structural-write    convert-to-issue | convert-to-subtask | reparent
credentials         env CUBRID_JIRA_USER + CUBRID_JIRA_PASSWORD
                    (no interactive prompt; falls back to ~/.netrc)
                    reads (incl. attachment) fall back to anonymous access
cache directory     $CUBRID_JIRA_DIR  ||  ~/.local/share/cubrid-jira/issues/
                    (default server is flat; alternates use <hostname>/)
attachments dir     --out || <state>/attachments/[<alternate-host>/]<KEY>
output (stdout)     markdown or JSON
output (stderr)     status + errors
machine-readable    add `--output json` to write subcommands or to `jql`
                    → exactly one JSON object on stdout
exit codes          0 ok | 1 generic | 2 401 or argparse usage error | 3 403 | 4 404 | 5 400
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

## Create priority

CBRD's create screen rejects `priority` (400 "not on the appropriate screen"),
but the edit screen allows it. `create --priority` therefore omits it from the
POST and sends `PUT /rest/api/2/issue/{key}` with only the priority right
after, before link requests (dry-run shows both). If that PUT fails, stderr
names the new key, `--output json` still emits `key` plus `priority_error`,
links are skipped, and the exit code is the PUT's HTTP-mapped code.

## Create links

Link POSTs run in order (relates, then blocks). The first failure stops the
command: no later link, no retry, no cache refresh. stderr names the new key,
the created links, and a `cubrid-jira link <KEY> --type T --to OTHER --yes`
repair command per unsent link (failed one included). `--output json` keeps
`key`/`id`/`self`/`url` and adds `link_error`, `links_created`, `links_unsent`
(lists of `{"type", "to"}`); exit is the failed request's HTTP-mapped code.
Priority and link failures share `_exit_after_partial_create` in `cli.py`;
a priority failure lists all requested links as unsent.

## Transition resolutions

`transition` fetches `…/transitions?expand=transitions.fields`. If the chosen
transition carries a `resolution` field, `--resolution NAME` is **mandatory**
(case-insensitive match on `allowedValues`, sent by id as
`fields.resolution`), because JIRA otherwise applies the screen default
(`Fixed`) silently and the resolution cannot be edited in place afterwards
(`editmeta` omits it). A missing/unknown name, or `--resolution` on a
transition without the field, exits 1 listing the allowed names — in dry-run
too. Dry-run/live JSON add `"resolution": {"id", "name"}`. List-mode JSON
(no `--to`) drops each transition's bulky `fields` and instead gives a
transition that carries one `"resolution": {"required": bool, "allowed": [names]}`;
`to` is cut to `{id, name}`.

## CRITICAL write-safety rules

- **All writes are dry-run by default.** `--yes` is required to actually send. Treat the absence of `--yes` as a no-op.
- **On HTTP 401, never retry.** Jira Server locks accounts and triggers a CAPTCHA after a few failed basic-auth attempts. The client exits 2 immediately; any retry loop in user code is a footgun. Reset the CAPTCHA via the web UI before re-running.

## Module layout (`src/cubrid_jira/`)

```text
cli.py        parent argparse + dispatch + payload builders
http.py       JiraClient (basic-auth, dry-run, retries) + fetch_issue / search_issues / download_file (GET, auth when creds resolve, else anon; 401 fails fast → exit 2)
session.py    SessionClient — JSESSIONID cookies + X-Atlassian-Token for wizard POSTs
wizard.py     pure HTML parsing + form-payload builders for the Convert wizard
markdown.py   Jira-wiki → markdown rendering; pure
walk.py       recursive related-issue traversal + save_issue cache write
auth.py       env → netrc → error credential resolution
cache.py      cache dir resolve + prefix-safe invalidation
server.py     server default + normalization + browse-URL inference
legacy.py     deprecation shims for cubrid-jira-search / cubrid-jira-fetch
```

Layering invariants (enforced by `tests/test_layering.py`):

- `markdown.py` does **not** import `urllib` — rendering is pure.
- `wizard.py`   does **not** import `urllib` — parsing is pure.
- `http.py`    does **not** import `subprocess` — no pandoc/process spawning.
- `session.py` does **not** import `subprocess` — same rule as http.py.
- `walk.py` is the only *library* module allowed to mix the HTTP and markdown
  layers; `cli.py` is the composition root and may wire any layers together
  (e.g. `jql` calls `http.search_issues` then `markdown.format_search_results_markdown`).

The legacy import path `cubrid_jira_fetcher` re-exports `cubrid_jira` and emits a `DeprecationWarning`; do not write new code against it.

## Tests

```sh
uv run pytest              # unit + mocked integration (a few seconds)
uv run pytest -m live      # also hits the real jira.cubrid.org (read-only)
```

## User docs

`README.md` is the landing page. User-facing behavior lives in `docs/`:
`commands.md` (flags, behavior, cache effects), `output-and-exit-codes.md`
(JSON shapes, exit codes), `setup.md` (install, credentials, servers, paths),
`recipes.md`, and `agent-guide.md` (the contract for agents that call the CLI
from other repositories). When a change alters what a user or a calling agent
sees, update the matching page in the same commit.
`tests/test_docs_examples.py` fails when a documented command stops parsing.

## Agent skills

### Issue tracker

Track CLI development issues and specs in GitHub Issues for `vimkim/cubrid-jira`. Before creating or fetching tickets, read `docs/agents/issue-tracker.md`.

### Triage labels

Before assigning triage roles or publishing agent-ready tickets, read `docs/agents/triage-labels.md` for the label mapping.

### Domain docs

Use a single domain context: root `GLOSSARY.md` and `docs/adr/`. Before exploring code, read `docs/agents/domain.md` for document and vocabulary rules.
