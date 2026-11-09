# Output and exit codes

This page describes what `cubrid-jira` writes to stdout and stderr, the JSON
shape of each command, and what each exit code means. Scripts and agents can
rely on all three.

## Streams

- stdout carries the result: Markdown, a table, or JSON.
- stderr carries progress, warnings, dry-run banners, and errors.

You can pipe stdout without filtering. In text mode, a dry run prints the
request line and headers on stderr and the JSON request body on stdout.

## JSON output

Every write command, `jql`, `attachment`, `comment-list`, and
`cubrid-jira-kanban` accept `--output json`. With it, stdout holds exactly one
JSON object on one line. Errors still go to stderr.

### Dry-run shape

A write command without `--yes` prints the plan:

```json
{"dry_run": true, "requests": [{"method": "POST", "url": "https://jira.cubrid.org/rest/api/2/issue/CBRD-27984/comment", "body": {"body": "h2. Repro\n..."}}]}
```

`requests` lists every request the live run would send, in order, with
`method`, `url`, and `body`. A `create` with `--priority` and two links returns
four requests. A `reparent` returns six wizard POSTs.

### Live shapes

| Command | Live result |
|---|---|
| `create` | `{"key": "CBRD-9999", "id": "...", "self": "...", "url": "https://jira.cubrid.org/browse/CBRD-9999"}` |
| `comment` | `{"issue": "CBRD-1", "comment_id": "42"}` |
| `comment-update` | `{"issue": "CBRD-1", "comment_id": "42", "updated": true}` |
| `comment-delete` | `{"issue": "CBRD-1", "comment_id": "42", "deleted": true}` |
| `link` | `{"inward": "CBRD-1", "outward": "CBRD-2", "type": "Relates"}` |
| `transition --to` | `{"issue": "CBRD-1", "transition_id": "21", "to": "In Progress"}` |
| `assign` | `{"issue": "CBRD-1", "assignee": "jdoe"}`. `assignee` is `null` after `--to ""`. |
| `update` | `{"issue": "CBRD-1", "updated_fields": ["description", "summary"]}` |
| `convert-to-issue` | `{"issue": "CBRD-1", "type": "Task", "previous_parent": "CBRD-X"}` |
| `convert-to-subtask` | `{"issue": "CBRD-1", "parent": "CBRD-Y", "type": "Sub-task"}` |
| `reparent` | `{"issue": "CBRD-1", "from_parent": "CBRD-X", "to_parent": "CBRD-Y"}` |

When a transition carries a resolution, both the dry-run and the live result
add `"resolution": {"id": "7", "name": "Not a bug"}`.

### Read shapes

| Command | Result |
|---|---|
| `jql` | The raw `/rest/api/2/search` response from JIRA. |
| `comment-list` | `{"issue", "total", "comments": [{"id", "author", "created", "body"}]}`. `author` is the display name. |
| `transition` without `--to` | `{"issue", "transitions": [...]}`. Each transition has `id`, `name`, and `to` cut to `{id, name}`. A transition that carries a resolution adds `"resolution": {"required": true, "allowed": [names]}`. |
| `attachment` | `{"issue", "count", "out_dir", "attachments": [...]}`. Each entry has `filename`, `size`, `mimeType`, `path`, `downloaded`, and `skipped` when the file was not saved. `--list` entries have `filename`, `size`, `mimeType`, and `content`, and the object has no `out_dir`. |
| `cubrid-jira-kanban` | `{"total", "groups": [{"status", "count", "issues"}]}` |

### Partial create

`create` can fail after the issue exists, when the priority request or a link
request fails. The JSON result then keeps `key`, `id`, `self`, and `url`, and
adds:

| Field | When | Meaning |
|---|---|---|
| `priority_error` | The priority PUT failed. | A short message that names the failed request and its exit code. stderr has JIRA's error. |
| `link_error` | A link POST failed. | The same kind of message for the failed link. |
| `links_created` | Either failure. | Links already created, as `[{"type", "to"}]`. |
| `links_unsent` | Either failure. | Links not sent, the failed one included, as `[{"type", "to"}]`. A priority failure lists every requested link. |

The exit code is the code of the failed request. stderr prints one repair
command per unsent request.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success, or the dry run finished. | |
| 1 | Generic error: bad input, a parse failure, network errors after the retry, a refused redirect, a stale wizard token, or the `reparent` atomicity warning. | Read stderr. For `reparent`, run the recovery command it prints. |
| 2 | HTTP 401, authentication failed. | Stop. Do not retry. See [401 and the CAPTCHA lockout](#401-and-the-captcha-lockout). |
| 3 | HTTP 403. The account lacks permission. | Ask a JIRA admin or use another account. |
| 4 | HTTP 404. The issue or resource does not exist. | Check the key and the `--server`. |
| 5 | HTTP 400. JIRA rejected the request. stderr prints JIRA's `errors` and `errorMessages`. | Fix the field values. A malformed JQL query also exits 5. |

Field writes, `search`, `jql`, and `comment-list` map HTTP errors to these
codes. `search` maps a failure of the issue you asked for. It skips a related
issue that fails, unless the failure is a 401. These cases differ:

- `attachment` finds the issue through a JQL query, so a missing key is an
  HTTP 400 and exits 5.
- The structural writes map a 401 from any wizard step to exit 2. Any other
  HTTP error from a wizard step ends with a Python traceback and exit 1. The
  exception is the second half of `reparent`, which prints the atomicity
  warning instead.
- Dry runs of `comment`, `comment-update`, `comment-delete`, `link`, `assign`,
  and `update` send no request, so they exit 0 for a key that does not exist.
  Dry runs of `transition` and the structural writes read the issue first and
  exit 4 for a missing key.

## Retries and redirects

These rules cover reads and field writes. The Convert wizard steps of the
structural writes get no retry and no redirect check.

- A 5xx response or a transient network error gets one short retry. If that
  also fails, the command exits with code 1.
- A 401 is never retried.
- A write that gets a redirect fails with exit code 1 and sends nothing. The
  CLI does not follow it, because `urllib` turns a redirected POST into a GET
  that looks like success. The error names the redirect target and the
  `--server` value to rerun with.
- When the wizard rejects a stale `atl_token`, the command exits with code 1
  and names the step that failed.

## 401 and the CAPTCHA lockout

JIRA Server 7.7.1 locks an account after a few failed basic-auth attempts and
then demands a CAPTCHA in the web UI. The count is per account and adds up
across requests and commands. For that reason the CLI sends one authenticated
attempt and exits with code 2 on the first 401.

After `Error: Auth failed (HTTP 401)`:

1. Stop. A retry loop locks the account.
2. Log in to `https://jira.cubrid.org` in a browser and solve the CAPTCHA.
3. Fix `CUBRID_JIRA_USER`, `CUBRID_JIRA_PASSWORD`, or `~/.netrc`.
4. Run the command once more.

## Check the examples on this page

The JSON samples above are shortened. To see a real dry-run plan without
sending anything, run:

```sh
cubrid-jira comment CBRD-27984 --body-file note.md --output json
```
