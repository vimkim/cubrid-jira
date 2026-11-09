# Agent guide

This guide is for an agent that runs `cubrid-jira` from any repository. An
agent that changes this repository's code reads [`CLAUDE.md`](../CLAUDE.md).

## Contract

```text
command      cubrid-jira <subcommand> [args]; `cubrid-jira <subcommand> --help` lists flags
reads        search KEY | jql "QUERY" | attachment KEY | comment-list KEY | transition KEY
             GET only. Run them freely.
writes       create | comment | comment-update | comment-delete | link | transition --to
             | assign | update | convert-to-issue | convert-to-subtask | reparent
gate         a write without --yes is a dry run: it prints the plan and sends nothing
credentials  CUBRID_JIRA_USER + CUBRID_JIRA_PASSWORD, else ~/.netrc. No prompt.
             search, jql, and attachment fall back to anonymous.
             Every other command needs them, dry runs included.
stdout       the result. With --output json: exactly one JSON object.
stderr       progress, dry-run banners, warnings, errors
exit         0 ok | 1 generic | 2 HTTP 401 or usage error | 3 HTTP 403 | 4 HTTP 404 | 5 HTTP 400
server       https://jira.cubrid.org by default (CBRD, CUBRIDQA).
             RND issues: --server http://jira.cubrid.com. The key prefix does not pick the server.
```

## Send a write

1. Run the write without `--yes`, with `--output json`. The result is the plan:
   `{"dry_run": true, "requests": [{"method", "url", "body"}]}`.
2. Check the plan: the target key, the server in each `url`, and the converted
   body. Markdown bodies arrive as Jira wiki markup.
3. Rerun the identical command with `--yes` only when the user asked for this
   change, or when the calling workflow says its request already authorizes it.
   Otherwise show the plan and ask.
4. Read the issue back with `cubrid-jira search KEY --no-recurse`. The write
   deleted the cached copy, so `search` fetches the live issue.

The write is done when the read-back shows the change. For a description
update, the three commands are:

```sh
cubrid-jira update CBRD-XXXXX --description-file report.md --output json
cubrid-jira update CBRD-XXXXX --description-file report.md --output json --yes
cubrid-jira search CBRD-XXXXX --no-recurse
```

## Stop at the first 401

Exit code 2 without a `usage:` line on stderr means JIRA rejected the
credentials. Report the error to the user
and stop. JIRA Server counts every failed basic-auth request against the
account and locks it behind a web CAPTCHA after a few, so a second attempt, a
retry loop, or another command with the same credentials makes it worse. The
user fixes the credentials. You run the command again only after they say so.

## Act on the exit code

| Exit | Action |
|---|---|
| 0 | Continue. |
| 1 | Read stderr. It names the bad input, the refused redirect and the `--server` to use, or the recovery command. |
| 2 | If stderr has a `usage:` line, the arguments are wrong. Fix them. Otherwise stop. See [Stop at the first 401](#stop-at-the-first-401). |
| 3 | Report that the account lacks permission. |
| 4 | Check the key and the server. An `RND` key on the default server is not found. |
| 5 | JIRA rejected a field or a query. stderr holds JIRA's error messages. Fix the value and repeat the dry run. |

`attachment` exits 5, not 4, for a missing key, because it finds the issue
through a JQL query. Dry runs of
`comment`, `comment-update`, `comment-delete`, `link`, `assign`, and `update`
send no request, so a wrong key fails only on `--yes`. Read the issue with
`search` first when the key came from text you did not verify.

## Finish a partial write

Some writes can fail halfway. Each one prints how to finish. Run that repair
command once, after a dry run of it.

- **`create` fails after the issue exists.** The exit code is nonzero, yet the
  JSON result holds `key` and one of `priority_error` or `link_error`, plus
  `links_unsent`. The issue exists. Finish it with the `update` or `link`
  commands that stderr prints. Running `create` again makes a duplicate issue.
- **`reparent` prints `!!! ATOMICITY WARNING`.** The issue is now a Task with no
  parent. Run the printed `convert-to-subtask` command. After any other nonzero
  exit from `reparent --yes`, read the issue with `search` before you act, since
  some failures skip the warning.
- **`transition` exits 1 and lists resolution names.** The transition sets a
  resolution, and JIRA cannot change a resolution afterwards. Use the name the
  user gave. If they gave none, show them the list and ask. Pass the name with
  `--resolution`.

## Read efficiently

- `cubrid-jira search KEY` prints Markdown and caches one level of related
  issues. Add `--no-recurse` when you need only the one issue.
- `cubrid-jira search KEY --cache-only` reads the cache without the network.
- `cubrid-jira jql "QUERY" --output json` returns JIRA's search response.
  Paginate with `--max` and `--start-at`.
- `cubrid-jira attachment KEY --list` shows sizes before you download. Downloads
  skip files over `--max-bytes`, 5 MiB by default.
- `cubrid-jira transition KEY` lists transitions and their resolution names.
  Without `--to` it changes nothing.

## Install the skill

The [`cubrid-jira` skill](https://github.com/vimkim/my-cubrid-skills/tree/main/cubrid-jira)
teaches an agent two workflows on top of this contract: read an issue by key,
and publish a reviewed Markdown file as an issue description. The publish
workflow runs a link checker from the `cubrid-common` skill in the same
repository. Install both with the [skills CLI](https://github.com/vercel-labs/skills):

```sh
npx skills add vimkim/my-cubrid-skills --skill cubrid-jira cubrid-common
```

## Look up details

- Flags and behavior of each command: [Command reference](commands.md).
- JSON shapes and exit codes: [Output and exit codes](output-and-exit-codes.md).
- Step-by-step tasks: [Recipes](recipes.md).
- Credentials, servers, and file locations: [Set up cubrid-jira](setup.md).
