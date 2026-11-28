---
name: cubrid-jira
description: Look up CUBRID JIRA issue context, choose an issue's status transitions, and publish a reviewed Markdown file as an existing issue's description. Use when a CBRD-XXXXX ticket is mentioned, when the user asks about a JIRA issue, when recommending or running a JIRA transition such as resolving after a PR merge, or when another authorized workflow needs to upload a finished issue report.
---

Read or update CUBRID JIRA issues with the `cubrid-jira` CLI.

Before interpreting an issue's assignee or recommending work, apply the user's current instructions and any configured CUBRID workflow policy (such as the `cubrid-common` skill if installed). Establish the current user's identity once per task from explicit conversation context or `gh api user --jq .login`, and preserve the raw issue output separately from that interpretation.

## Preflight

Verify `cubrid-jira` exists in PATH by running `command -v cubrid-jira`. If it does not exist, halt immediately and tell the user:

```text
Error: `cubrid-jira` is not installed.

Install it:
    uv tool install git+https://github.com/vimkim/cubrid-jira
    # or from a local checkout: just install
```

Require a concrete issue key such as `CBRD-25123`. If none is available, ask for one.

## Read an issue

Run:

```bash
cubrid-jira search TICKET_ID
```

Present the output as-is. Search contacts JIRA by default and refreshes the local cache. If the command fails after preflight, report the exact error rather than guessing.

## Resolve an issue after its fix merges

When a PR that fixes the issue merged normally, the issue's target is status `Resolved` with resolution `Fixed`, even when the issue skipped Analysis or Develop. Walk the fix route forward from the current status:

| Current status | Transition | New status |
|---|---|---|
| `Confirmed` or `Analysis` | `Start Develop` | `Develop` |
| `Develop` | `Accept the fix` | `Handover` |
| `Handover` | `Check in fix` with `--resolution Fixed` and `Fix Version/s` | `Resolved` |

Transition names depend on the current status, and JIRA lists ` Check in fix` with a leading space (`--to` matches names case-insensitively without the surrounding spaces). Before each step, list the live transitions and their target statuses, and take the one this table names:

```bash
cubrid-jira transition TICKET_ID --output json
```

`Check in fix` also needs `Fix Version/s` (`fixVersions`). A workflow validator enforces it, so JIRA marks the screen field optional and returns HTTP 400 only on the live send. `cubrid-jira` reads the issue during the dry run and exits 1 with `requires Fix Version/s` when the issue has none. Pass the version the fix landed in, which is `guava` for `11.5`/`develop` work:

```bash
cubrid-jira transition TICKET_ID --to "Check in fix" --resolution Fixed --fix-version guava
```

To set it on its own, run `cubrid-jira update TICKET_ID --fix-version guava`. That replaces the whole list. `--field fixVersions=guava` and `--field "Fix Version/s=guava"` do the same.

`Resolve without fix` and `Ask Reconfirmation` record the issue as unfixed or send it back to the reporter, so they fit only an issue whose fix did not land. An issue already `Resolved` or `Closed` needs no transition. For any other status, such as `Open`, report the status and the listed transitions to the user.

After `/cubrid-pr-merge`, `pr-final-merge-handoff cleanup --pr PR --jira-resolve-fixed` previews this whole walk for the PR's tickets; `--apply` runs it. It does not pass `--fix-version`, so when its preview reports `requires Fix Version/s` at `Check in fix`, run `cubrid-jira update TICKET_ID --fix-version VERSION` first (a live write that needs the same approval), then preview again. Elsewhere, such as a TC-only change whose carrier engine PR closed unmerged, run each step with `cubrid-jira transition TICKET_ID --to NAME`.

Every transition is a live JIRA write. Show the dry-run and send it (`--yes` or `--apply`) only when the user asks for it or approves it in this session.

## Affects Version/s rule (`Correct Error` in `CBRD`, `APIS`, `TOOLS`)

`CBRD`, `APIS`, and `TOOLS` require `Affects Version/s` (`versions`) when creating a `Correct Error` issue or triaging it (`Open` -> `Confirmed`). Other issue types (`Improve Function/Performance`, `Refactoring`, `Task`, `Sub-task`, etc.) do not require it.

- For defects found on the `11.5` development branch (`develop`), pass `--affects-version guava` (`cubrid-jira` also normalizes `11.5` to `guava` on `create`, `update`, and `transition`).
- `cubrid-jira create` enforces this in dry-run mode for `CBRD`, `APIS`, and `TOOLS` when `--type "Correct Error"`.
- During triage transitions whose screen exposes `versions`, pass `--affects-version VERSION` to `cubrid-jira transition` (or update it with `cubrid-jira update KEY --affects-version VERSION`).

## Check for duplicates before a write

Before you create an issue or publish a description, run the `cubrid-jira-dup` skill (or `cubrid-jira-dup candidate-find` if installed) on the Markdown file. Skip it when the calling skill already ran it on the same file content. Report the result with the dry-run output. The check does not block the write and does not replace the user's authorization.

## Publish a Markdown description

Use this mode only when the user explicitly asks to upload the specified issue report, or when a calling skill states that its triggering request pre-authorizes publication. Do not request another confirmation in the pre-authorized case.

Ensure every URL in material intended for other readers is accessible to those readers (such as verified GitHub, JIRA, or CI URLs) and never embeds private or personal viewing hosts (such as `dev2.baboon-rudd.ts.net`). Resolve `link_checker` to `scripts/check-shared-links.py` inside this skill's directory (for example `${CLAUDE_SKILL_DIR:-$HOME/.agents/skills/cubrid-jira}/scripts/check-shared-links.py`). Validation is read-only; report failures and return necessary changes to review rather than rewriting an approved file.

1. Require an existing Markdown file. Extract the complete leading issue key with `^([A-Z][A-Z0-9]*-[0-9]+)(?=[_.-]|$)` and require exact equality with the target key. For example, `CBRD-12345` must not match a file for `CBRD-123456`. Reject a missing key or mismatch. Run `python3 "$link_checker" ISSUE_FILE` on that exact file and stop on failure.
2. Fetch the current issue with `cubrid-jira search TICKET_ID` to verify that the target exists and record its summary and description. This workflow replaces only the description. A local title heading is a proposed title or description heading; changing the live summary requires separate explicit authorization and a summary-field update workflow.
3. Run the default dry-run, inspect its resolved target and converted description, and retain that converted payload for verification:

   ```bash
   cubrid-jira update TICKET_ID \
     --description-file ISSUE_FILE \
     --from markdown \
     --output json
   ```

4. If publication is authorized and the dry-run target is correct, rerun `python3 "$link_checker" ISSUE_FILE` immediately before the live update and stop on failure. If it passes, perform the live update without pausing:

   ```bash
   cubrid-jira update TICKET_ID \
     --description-file ISSUE_FILE \
     --from markdown \
     --yes \
     --output json
   ```

5. Read the live description with `cubrid-jira search TICKET_ID` and compare it with the approved dry-run converted payload. Allow only documented normalization, not missing or changed content. If search exposes only rendered text, compare headings, links, tables, code blocks, and meaning as applicable, and report that raw payload equality was not verified. Report the issue URL and comparison result. A mismatch is partial completion. Report the differences without silently rewriting the approved file or claiming publication is verified.

Without explicit or delegated authorization, stop after the dry-run and ask before adding `--yes`. If any command fails, report the exact error and do not claim that the issue was uploaded.

$ARGUMENTS
