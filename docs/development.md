# Development

This guide sets up a checkout, runs the tests, and installs your working copy
as the global `cubrid-jira` command.

## Set up a checkout

```sh
git clone https://github.com/vimkim/cubrid-jira.git
cd cubrid-jira
uv sync --dev
uv run cubrid-jira search CBRD-26463 --no-recurse
```

## Run the tests

```sh
uv run pytest
uv run pytest -m live
```

`uv run pytest` runs the unit and mocked-integration tests in a few seconds.
`-m live` adds tests that read from `jira.cubrid.org`. They never write.

`tests/test_docs_examples.py` parses every `cubrid-jira` command in the `sh`,
`bash`, and `console` code blocks of the README and the user docs. If you rename
or remove a flag, that test names each doc line that still uses it. Put a
synopsis with placeholders, such as `[--parent KEY]`, in a `text` block.

## Check markup against Jira's renderer

`tests/test_render_oracle.py` checks the write path against the real Jira
renderer instead of hand-guessed expectations. `tests/markup_cases.py`
generates a few hundred small cases from a spec (a chain of inline markers,
such as bold around code, plus the character that touches each marker). The
spec alone yields the Markdown source, the Jira source, and the HTML Jira must
render. A second group of hand-written cases (`HOSTILE`) uses code text that
fights the fixers: `col_이름` after `my_table의`, `a*b`, backticks and braces
inside code, literal `*` and `_` pairs between Hangul, a `{code}` macro on the
same line as content, and `{code}` or
`{noformat}` tags that are escaped, mentioned in prose, or never closed. Each case's
converted output is looked up in
`tests/data/jira_render_snapshot.json`, a recorded map from markup to the HTML
that `jira.cubrid.org` rendered for it. The comparison ignores whitespace
outside code, so spaces the fixer inserts next to a span are fine. Text inside
`<tt>` and `<pre>` must match exactly, so a space inserted into code fails.
Lost or changed markup fails too.

The tests also check that `find_markup_contacts` flags exactly the
markup that Jira renders with a literal `{{` or `}}`, and that identifiers such
as `foo_bar_baz` pass through unchanged. These tests read the snapshot, so they
need no network.

Refresh the snapshot with `just render-snapshot` when you:

- add or change cases in `tests/markup_cases.py`, or
- change what the normalizer or the Markdown conversion outputs.

The script renders only the markup the snapshot lacks, drops entries nothing
uses any more, and rerunning it renders nothing new. Add `--all` to render
everything again. It needs network access but not credentials, and it stops on
a 401 without retrying. `uv run pytest -m live -k snapshot_matches` renders a
sample again and fails if the server's answers have drifted from the snapshot.

The snapshot file records the pandoc version that wrote the Markdown cases'
keys (`"pandoc"`, next to `"renders"`). Another pandoc version may write other
text, so on a machine with a different version the Markdown cases are skipped,
and the skip reason names both versions. They are not failures. The same rule
applies to the script: with a different version, or no pandoc that reads Jira,
it renders only the other cases, drops nothing, and keeps the recorded
version. To move the snapshot to your pandoc, run
`just render-snapshot --adopt-pandoc`. It records your version and drops the
Markdown entries it does not need, so run it on purpose, then commit the file.
The current snapshot was written by pandoc 2.14.0.3.

## Use just

The `justfile` wraps the common commands. Run `just` to list them.

| Recipe | What it does |
|---|---|
| `just setup` | `uv sync --dev`. Leaves the global command alone. |
| `just check` | Runs the non-live tests and checks that the CLI starts. |
| `just test -k server` | Runs pytest with your options. |
| `just test-live` | Runs only the live read-only tests. |
| `just render-snapshot` | Renders new markup oracle cases with Jira and updates the snapshot. |
| `just install` | Installs a global command that follows edits in this checkout. |
| `just install-copy` | Installs an independent global copy. Rerun it after source changes. |
| `just repair-install` | Points the global command at this checkout again after you move it. |
| `just sync` | Reinstalls all commands from this checkout and checks that they start. |
| `just doctor` | Prints the checkout, the imported package path, and whether the global command starts. |
| `just search CBRD-26463 --no-recurse` | Runs `search` from this checkout. |
| `just run --help` | Runs any `cubrid-jira` command from this checkout. |

The `write-dry-*` recipes only preview requests. `just run` keeps the CLI's
dry-run default and sends a write only when you include `--yes`.

Do not install with `pip install -e .`. Use `just install` instead.

## Find your way around the code

[`CLAUDE.md`](../CLAUDE.md) holds the module layout, the layering rules that
`tests/test_layering.py` enforces, and the write-safety rules for code changes.
Read it before you change `src/cubrid_jira/`.

When you change behavior that users can see, update the matching page in the
same commit:

| Change | Page |
|---|---|
| A flag, a subcommand, or what a command does | [Command reference](commands.md) |
| JSON output, an exit code, or retry behavior | [Output and exit codes](output-and-exit-codes.md) |
| Install, credentials, servers, or file locations | [Set up cubrid-jira](setup.md) |
| The rules an agent must follow | [Agent guide](agent-guide.md) |

## Background notes

These notes record why some behavior exists. They are dated and are not
updated as the code changes.

- [Reparenting sub-tasks via the Convert wizard](reparent-subtasks-via-convert-wizard.md)
  and its script [`reparent_via_wizard.py`](reparent_via_wizard.py).
- [Authenticated reads for non-public projects](authenticated-reads-for-nonpublic-projects.md).
- [Fix `create` failing on required custom fields](fix-create-missing-custom-fields.md).
- [Markdown comment upload confusion](markdown-comment-upload-confusion.md).
- [Review of tw-kang PRs #2, #3, #4 (2026-07-30)](review-2026-07-30-tw-kang-prs.md)
  and its [required fixes](required-fixes-2026-07-30.md).

Issues and specs for the CLI live in
[GitHub Issues](https://github.com/vimkim/cubrid-jira/issues).
