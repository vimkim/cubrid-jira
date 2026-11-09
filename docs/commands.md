# Command reference

This page lists every `cubrid-jira` subcommand, its flags, and what it does to
JIRA and to the local cache. `cubrid-jira <subcommand> --help` prints the same
flags from the installed version.

Commands fall into three groups:

| Group | Subcommands | Credentials | `--yes` |
|---|---|---|---|
| Read | `search`, `jql`, `attachment`, `comment-list`, `cubrid-jira-kanban` | Optional for `search`, `jql`, and `attachment`. Required for the others. | Not used |
| Field write | `create`, `comment`, `comment-update`, `comment-delete`, `link`, `transition`, `assign`, `update` | Required to send | Required to send |
| Structural write | `convert-to-issue`, `convert-to-subtask`, `reparent` | Required to send | Required to send |

For output shapes and exit codes, see
[Output and exit codes](output-and-exit-codes.md). For credentials, servers,
and the cache location, see [Set up cubrid-jira](setup.md).

## Read commands

Read commands send only GET requests. `search`, `jql`, and `attachment` attach
credentials when env vars or `~/.netrc` provide them, and fall back to
anonymous access otherwise, so public projects work without an account.
`comment-list` and `cubrid-jira-kanban` always need credentials.

### `search`

`search` prints one issue as Markdown on stdout.

```sh
cubrid-jira search CBRD-26463
cubrid-jira search https://jira.cubrid.org/browse/CBRD-26463
cubrid-jira search CBRD-26463 --no-recurse
cubrid-jira search CBRD-26463 --cache-only
cubrid-jira search RND-2851 --server http://jira.cubrid.com
```

| Flag | Default | Meaning |
|---|---|---|
| `issue` | | An issue key or a full browse URL. A `.com` browse URL selects `jira.cubrid.com`. |
| `--no-recurse` | off | Fetch only the requested issue. By default `search` also fetches one level of related issues into the cache. |
| `--cache-only`, `--offline` | off | Print the cached Markdown without contacting JIRA. Fails if the issue is not cached. |
| `--force` | | Deprecated. `search` already fetches live. |
| `-d`, `--dir DIR` | see [cache](setup.md#where-files-go) | Cache directory. |
| `--server URL` | `https://jira.cubrid.org` | JIRA installation. |

A normal `search` fetches the issue, overwrites `<KEY>.md` in the cache, and
prints the fresh Markdown. If the fetch fails, it exits nonzero and does not
print a stale cached copy.

### `jql`

`jql` runs a JQL query and prints the matching issues as a Markdown table.

```sh
cubrid-jira jql "project = CBRD AND created >= -7d" --max 100
cubrid-jira jql "fixVersion = guava" --output json | jq '.issues[].key'
cubrid-jira jql "project = RND" --server http://jira.cubrid.com
```

| Flag | Default | Meaning |
|---|---|---|
| `JQL` | | The query string. |
| `--fields` | `summary,status,issuetype,assignee,updated` | Fields to request. `--output json` uses the list as given. The text table always adds its display columns. |
| `--max N` | `50` | `maxResults`, the page size. Must be 0 or more. |
| `--start-at N` | `0` | `startAt`, the 0-based offset of the first result. |
| `--output {text,json}` | `text` | `text` prints a Markdown table headed `N of TOTAL matching issues`. `json` prints the raw `/rest/api/2/search` response on one line. |
| `--server URL` | `https://jira.cubrid.org` | JIRA installation. |

The table columns are key (linked), status, type, assignee, updated, and
summary. The CLI escapes pipes and newlines in cell values. A malformed query
is an HTTP 400 from the server and exits with code 5, not with zero results.

### `attachment`

`attachment` downloads an issue's attachments, or lists them with `--list`.

```sh
cubrid-jira attachment CBRD-26463 --list
cubrid-jira attachment CBRD-26463 --out ./attachments
cubrid-jira attachment CBRD-26463 --max-bytes 52428800 --output json
```

| Flag | Default | Meaning |
|---|---|---|
| `issue` | | An issue key or a full browse URL. |
| `--list` | off | Print size, MIME type, and filename for each attachment. Download nothing. |
| `--out DIR` | `<state>/attachments/<KEY>/` | Download directory. The CLI uses an explicit `--out` exactly as given. |
| `--max-bytes N` | `5242880` (5 MiB) | Skip any attachment larger than `N` bytes. The CLI counts received bytes, so a server that reports a wrong size cannot bypass the limit. Skipped files still appear in the manifest. |
| `--output {text,json}` | `text` | `text` prints one `[OK]` or `[SKIP]` line per file. `json` prints the manifest as one object. |
| `--server URL` | `https://jira.cubrid.org` | JIRA installation. |

The CLI keeps only the basename of each server-supplied filename, so a name
such as `../../x` cannot write outside the download directory. When two
attachments share a name, the second one gets a `-<id>` suffix. A 401 during a
download stops the whole command with exit code 2.

### `comment-list`

`comment-list` prints the most recent comments on an issue. It always contacts
JIRA and never changes the cache.

```sh
cubrid-jira comment-list CBRD-26463 --limit 5
cubrid-jira comment-list CBRD-26463 --output json | jq -r '.comments[].id'
```

`--limit N` keeps the `N` most recent comments (default 50). In JSON output,
each comment has `id`, `author` (the display name, such as `Daehyun Kim`, not
the username), `created`, and `body`.

### `cubrid-jira-kanban`

`cubrid-jira-kanban` is a separate binary. It lists every ticket assigned to
your account, grouped by status, and leaves out `Closed` and `Resolved`. It
needs credentials because it queries `currentUser()`.

```sh
cubrid-jira-kanban
cubrid-jira-kanban --output markdown > my-active-tickets.md
cubrid-jira-kanban --output json | jq '.groups[] | {status, count}'
cubrid-jira-kanban --server http://jira.cubrid.com
```

| Flag | Default | Meaning |
|---|---|---|
| `--output {text,markdown,json}` | `text` | Terminal tables, Markdown tables, or one JSON object with `total` and `groups`. Each group has `status`, `count`, and `issues`. |
| `--page-size N` | `100` | Issues per request. The command fetches every page, so this is not a result limit. |
| `--no-color` | off | Disable terminal colors. |
| `--server URL` | `https://jira.cubrid.org` | JIRA installation. |

Groups appear in this order: `Open`, `Reopened`, `Confirmed`, `Develop`,
`Test`, `Handover`, then other statuses alphabetically. Within a group, the
most recently updated ticket comes first. Below 100 terminal columns the text
view hides the type column. If a page request fails, a ticket repeats, the
total changes, or a page comes back empty too early, the command exits nonzero
and prints no partial board.

## Write commands

Every write command is a dry run unless you pass `--yes`. A dry run prints the
resolved URL, masked headers, and JSON body of each request it would send, and
sends nothing. To send, run the same command again with `--yes`.

Write commands need credentials even for a dry run. Without them, the command
exits with code 1 and prints what to set.

The write examples below use placeholder keys such as `CBRD-XXXXX`, so that
copying one with `--yes` cannot change a real issue by accident.

All write commands accept these flags:

| Flag | Default | Meaning |
|---|---|---|
| `--yes` | off | Send the requests. Without it the command is a dry run. |
| `--dry-run` | on unless `--yes` | Print the planned requests. This is already the default. |
| `--output {text,json}` | `text` | `json` prints exactly one JSON object on stdout. |
| `--server URL` | `https://jira.cubrid.org` | JIRA installation. Use `http://jira.cubrid.com` for `RND` issues. |
| `-d`, `--dir DIR` | see [cache](setup.md#where-files-go) | Cache directory to update or invalidate after the write. |

### Field writes

```text
cubrid-jira create         --project KEY --type TYPE --summary TEXT
                           [--parent KEY] [--description-file PATH]
                           [--priority NAME] [--assignee USER]
                           [--label L ...] [--component C ...]
                           [--link-relates KEY] [--link-blocks KEY]
                           [--field FIELD=VALUE ...] [--from {markdown,jira}]
cubrid-jira comment        ISSUE --body-file PATH [--from {markdown,jira}]
cubrid-jira comment-update ISSUE --id COMMENT-ID --body-file PATH [--from {markdown,jira}]
cubrid-jira comment-delete ISSUE --id COMMENT-ID
cubrid-jira link           ISSUE --type {Blocks,Cloners,Duplicate,Relates} --to KEY
cubrid-jira transition     ISSUE [--to NAME] [--resolution NAME]
cubrid-jira assign         ISSUE --to USER
cubrid-jira update         ISSUE [--summary TEXT] [--description-file PATH]
                           [--priority NAME] [--label L ...] [--component C ...]
                           [--field FIELD=VALUE ...] [--from {markdown,jira}]
```

#### `create`

`create` creates an issue. In text mode it prints `Created <KEY>: <URL>` on
stderr. With `--output json`, the key and URL are in the JSON result on stdout.

```sh
cubrid-jira create --project CBRD --type Bug --summary "csql crashes on an empty table" \
  --description-file bug.md --priority Major --link-relates CBRD-XXXXX
```

- `--type Sub-task` requires `--parent KEY`. The CLI rejects a missing or
  malformed parent key before sending anything. If a `--field` also resolves
  to `parent`, its value must match `--parent`.
- `--priority` is set by a second request. The `CBRD` create screen rejects
  `priority` with HTTP 400, so the CLI sends the POST without it and then a
  `PUT /rest/api/2/issue/<key>` that sets only the priority. The dry run shows
  both requests.
- `--link-relates` and `--link-blocks` send one link request each, in that
  order, after the priority request.
- `--priority` accepts `Blocker`, `Critical`, `Major`, `Minor`, or `Trivial`.

If the priority request or a link request fails after the issue exists, the
command stops at that request. It does not retry, it sends no later link, and
it does not refresh the cache. stderr names the new key and prints a repair
command for each request that was not sent:

```text
cubrid-jira update <KEY> --priority NAME --yes
cubrid-jira link <KEY> --type TYPE --to OTHER --yes
```

The exit code is the code of the failed request. See
[partial create](output-and-exit-codes.md#partial-create) for the JSON fields.

#### `comment`, `comment-update`, and `comment-delete`

```sh
cubrid-jira comment CBRD-XXXXX --body-file note.md
cubrid-jira comment-update CBRD-XXXXX --id 123456 --body-file note.md
cubrid-jira comment-delete CBRD-XXXXX --id 123456
```

Get a comment ID from `comment-list --output json`. `comment-update` reads the
body from stdin when `--body-file` is `-`. `comment-delete` cannot be undone. In
a live run it prints `# About to DELETE comment ...` on stderr before it sends
the request.

#### `link`

```sh
cubrid-jira link CBRD-XXXXX --type Relates --to CBRD-YYYYY
```

`ISSUE` becomes the inward issue and `--to` the outward issue. `--type` must be
`Blocks`, `Cloners`, `Duplicate`, or `Relates`.

#### `transition`

```sh
cubrid-jira transition CBRD-XXXXX
cubrid-jira transition CBRD-XXXXX --to "In Progress"
cubrid-jira transition CBRD-XXXXX --to "Bug Invalid" --resolution "Not a bug"
```

Without `--to`, `transition` lists the transitions available from the current
status. `--to` matches a transition name without regard to case.

Some transitions, such as `Bug Invalid` and `Check in fix`, carry a
`resolution` field. If such a transition is sent without a resolution, JIRA
silently applies its screen default, `Fixed`, and the resolution cannot be
edited afterwards. So when the chosen transition carries a `resolution` field,
`--resolution NAME` is required, even if JIRA marks the field optional. The CLI
matches the name against the allowed values without regard to case and sends
it by id.

`transition` exits with code 1 and lists the allowed names when:

- the transition needs a resolution and `--resolution` is missing,
- the `--resolution` name is not an allowed value, or
- `--resolution` is given for a transition without a resolution field, or
  without `--to`.

This check also runs in a dry run, so you see the problem before you add
`--yes`. The transition list marks each transition that requires a resolution
and shows its allowed names.

#### `assign`

```sh
cubrid-jira assign CBRD-XXXXX --to jdoe
cubrid-jira assign CBRD-XXXXX --to ""
```

`--to` takes a JIRA username. An empty string clears the assignee.

#### `update`

`update` edits fields of an existing issue. Give at least one of `--summary`,
`--description-file`, `--priority`, `--label`, `--component`, or `--field`.

```sh
cubrid-jira update CBRD-XXXXX --description-file notes.md
cubrid-jira update CBRD-XXXXX --label oos --label vacuum
```

- `--label` and `--component` replace the whole list. To add one label, pass
  the existing labels too.
- `--description-file -` reads the description from stdin.

### Set any field with `--field`

`create` and `update` accept `--field FIELD=VALUE`, repeated as needed. Use it
for project-required fields such as `QA Scenario` on `CBRD`.

```sh
cubrid-jira update CBRD-XXXXX --field "Some Free Text=hello"
cubrid-jira create --project CBRD --type Task --summary "Measure vacuum lag" \
  --field 'QA Scenario={"value":"Not Required"}'
```

- `FIELD` is a field id such as `customfield_210565` or a display name such as
  `QA Scenario`. The CLI resolves names through `/rest/api/2/field` on first use
  and caches the map in `<cache_dir>/field-map.json`. Alternate servers use
  `<cache_dir>/<hostname>/field-map.json`.
- A `VALUE` that starts with `{` or `[` is decoded as JSON. Select,
  multi-select, cascading-select, user, and date fields need JSON. Any other
  value is sent as a plain string, which suits text fields.
- If two custom fields share a display name, the CLI exits with an error.
  Pass the field id instead.

To find the allowed options of a select field, read the create metadata:

```sh
curl -u "$CUBRID_JIRA_USER:$CUBRID_JIRA_PASSWORD" \
  'https://jira.cubrid.org/rest/api/2/issue/createmeta?projectKeys=CBRD&issuetypeNames=Task&expand=projects.issuetypes.fields' \
  | jq '.projects[0].issuetypes[0].fields["customfield_210565"].allowedValues'
```

### Markdown bodies

`--body-file` and `--description-file` take Markdown by default. The CLI
converts Markdown to Jira wiki markup with pandoc before it sends the body.
Pass `--from jira` when the file already contains Jira wiki markup.

The conversion handles these cases:

- **Korean spacing.** JIRA Server fails to parse an inline marker that is
  followed directly by Hangul, such as `{{name}}의`. Both input formats get a
  spacing fix for this.
- **Code fence labels.** The CLI maps fence labels to the formatter names that
  `jira.cubrid.org` supports. `text`, `plaintext`, and any unknown label become
  `none`, so the issue does not show an unsupported-formatter error.
- **`{code}` inside code.** A code block that contains the literal `{code}` is
  sent as a `{noformat}` block, which has no syntax highlighting. A block that
  contains both `{code}` and `{noformat}` is rejected before sending, because
  either wrapper would close inside the code.
- **Tables.** Write pipe tables. The CLI rejects Pandoc simple tables (columns
  set by whitespace and a dashed ruler) with an error that names the line,
  because editing a cell past its ruler can change the table silently. `search`
  always prints JIRA tables as pipe tables, so a body you read can be edited and
  written back.

### Structural writes

These commands change an issue's type or parent:

```sh
cubrid-jira convert-to-issue CBRD-XXXXX
cubrid-jira convert-to-subtask CBRD-XXXXX --to CBRD-YYYYY
cubrid-jira reparent CBRD-XXXXX --to CBRD-YYYYY
```

| Subcommand | What it does | Refuses when | Cache entries invalidated |
|---|---|---|---|
| `convert-to-issue` | Sub-task to `--type` (default `Task`), dropping the parent. | The issue is not a Sub-task. | The issue and its previous parent. |
| `convert-to-subtask` | Issue to a Sub-task of `--to`. `--type` defaults to `Sub-task`. | The issue is already a Sub-task, or `--to` is a Sub-task. | The issue and the new parent. |
| `reparent` | Moves a Sub-task to the parent `--to`. | The issue is not a Sub-task, `--to` is its current parent, or `--to` is a Sub-task. | The issue, the old parent, and the new parent. |

#### Why these commands use the Convert wizard

On `jira.cubrid.org`, a `PUT /rest/api/2/issue/{KEY}` that sets
`fields.parent` returns HTTP 204 and changes nothing. The `CBRD` field
configuration leaves `parent` off the Sub-task edit screen, so JIRA drops the
field. These three commands drive the Convert wizard that the web UI uses
(`/secure/ConvertSubTask.jspa` and `/secure/ConvertIssue.jspa`) with a session
cookie and form POSTs. The full rationale, eight traps, and a curl-only smoke
test are in
[Reparenting sub-tasks via the Convert wizard](reparent-subtasks-via-convert-wizard.md).

Issue type ids differ between JIRA installations. On each live run the commands
read them from the wizard page's `<select name="issuetype">` and match by
name, so nothing is hard-coded.

#### Dry run

Without `--yes`, a structural write:

1. Fetches the issue's metadata with the same GET that `search` uses.
2. Runs the refusal checks in the table above.
3. Prints the planned wizard POSTs with `<extracted-at-runtime>` and
   `<resolved-at-runtime>` placeholders for `atl_token`, `guid`, and
   `issuetype`.

A dry run never logs in and never contacts a wizard endpoint.

#### `reparent` is atomic or loud

`reparent` converts the Sub-task to a Task, checks the result, and then
converts the Task to a Sub-task of `--to`. If anything fails after the first
wizard POST, the issue may be left as a Task with no parent. In that case
`reparent`:

- prints `!!! ATOMICITY WARNING` on stderr with what it knows about the state,
  a `cubrid-jira search <KEY> --no-recurse` command to check it, and the
  recovery command, `cubrid-jira convert-to-subtask <KEY> --to <NEW> --yes`,
- exits with code 2 if the cause was an HTTP 401, and with code 1 for any
  other cause, Ctrl-C included, and
- leaves the cache alone. The `search` command in the warning fetches the real
  state and refreshes the cache.

Read the issue first. If it is a Task with no parent, run the recovery command
once. Do not retry `reparent` in a loop.

## Cache effects

| Command | Cache effect after a live run |
|---|---|
| `search` | Overwrites `<KEY>.md` and the files of one level of related issues. |
| `search --cache-only` | None. Reads only. |
| `comment-list`, `jql`, `attachment` | None. |
| `create` | Fetches the new issue and saves it. With `--parent`, deletes the parent's cached files so its subtask list refreshes on the next read. |
| `comment`, `comment-update`, `comment-delete`, `transition`, `assign`, `update` | Deletes the issue's cached Markdown, so the next `search` fetches it. |
| `link` | Deletes the cached Markdown of both issues. |
| `convert-to-issue`, `convert-to-subtask` | Deletes two entries. See the table in [Structural writes](#structural-writes). |
| `reparent` | Deletes three entries, unless it prints the atomicity warning. |

## Deprecated binaries

| Binary | Use instead |
|---|---|
| `cubrid-jira-search` | `cubrid-jira search` |
| `cubrid-jira-fetch` | `cubrid-jira search`. `cubrid-jira-fetch KEY --depth N` still fetches related issues to depth `N` and writes them to `./related_issues/`, or to `-d DIR`. It overwrites existing files unless you pass `--skip-existing`. |

The Python import path `cubrid_jira_fetcher` re-exports `cubrid_jira` and
emits a `DeprecationWarning`. Import `cubrid_jira` instead.
