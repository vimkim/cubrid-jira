---
name: cubrid-jira-kanban-sync
description: "Sync active CUBRID JIRA kanban ticket statuses with live GitHub PR, review, CI, and merge state. Use when auditing or cleaning up your personal CUBRID JIRA kanban board, especially on Friday reviews or weekly wrap-ups. Triggers on phrases like '칸반 상태 정리해줘', 'JIRA 이슈 상태 정리', 'sync cubrid jira kanban', 'clean up my jira kanban'."
argument-hint: "[optional scope or filter]"
---

# CUBRID JIRA Kanban Status Sync

Audit active JIRA tickets assigned to the current user against live GitHub PR, team review, CI, and sub-task status. Show proposed transitions and edge cases first, then apply approved writes only.

Before interpreting assignees or PR readiness, apply the user's current instructions and any configured CUBRID workflow policy (such as the `cubrid-common` skill if installed).

## 1. Run preflight checks

Check that the required CLIs and the bundled approval checker exist:

```bash
command -v cubrid-jira-kanban cubrid-jira gh
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.agents/skills/cubrid-jira-kanban-sync}"
test -x "$SKILL_DIR/scripts/cubrid-pr-approval-check"
```

If `cubrid-jira-kanban` or `cubrid-jira` is missing, stop and tell the user to install `cubrid-jira` (`just install` from a local checkout or `uv tool install git+https://github.com/vimkim/cubrid-jira`). Check optional helpers `pr-final-merge-handoff`, `cubrid-xref`, and `cubrid-ci` when available.

Apply any scope or status filter passed in `$ARGUMENTS` when narrowing the audit.

## 2. Fetch active kanban issues

Run:

```bash
cubrid-jira-kanban --output json
```

Record each active ticket key, current status, issue type, `Target Version`, `Fix Version/s`, and summary. Keep `Closed` and `Resolved` tickets excluded.

## 3. Map linked GitHub PRs

Search `org:CUBRID is:pr` in batches of at most 4 ticket keys via `gh api graphql`:

```bash
q='org:CUBRID is:pr "CBRD-XXXXX" OR "CBRD-YYYYY"'
```

Join keys with `OR` without parentheses. This respects GitHub's 5-OR limit and avoids search parser failures. Match returned PRs by issue key in `title` and `headRefName`.

Use `cubrid-xref --json <KEY>` when a ticket needs full bundle or standalone TC PR resolution.

## 4. Check team review, CI, and EPIC sub-tasks

For each ticket with linked PRs, collect live evidence:

1. **Team review**: For every open non-draft PR, read `headRefOid` and run this skill's bundled approval checker:

   ```bash
   "$SKILL_DIR/scripts/cubrid-pr-approval-check" --repo "$REPO" --pr "$PR_NUMBER" --head "$HEAD_SHA"
   ```

   Do not treat aggregate `reviewDecision`, `mergeStateStatus`, or comment text as approval.
2. **Linked TC PRs**: For open `CUBRID/cubrid` PRs whose engine check passes, run `pr-final-merge-handoff plan --pr "$PR_NUMBER" --json` when available. Check that every changed `tc/pr-<N>` PR (`files_changed > 0`) is non-draft and passes approval.
3. **CI checks**: Run `gh pr checks "$PR_NUMBER" --repo "$REPO"`. Use `cubrid-ci status "$PR_NUMBER"` when available and you need detailed `gha-ci` run freshness.
4. **EPIC sub-tasks**: For `Development Subject` or parent tickets, count open sub-tasks:

   ```bash
   cubrid-jira jql "parent = $KEY AND status NOT IN (Closed, Resolved)" --output json
   ```

   Pass `--no-recurse` to `cubrid-jira search` when reading single-issue metadata to avoid recursive parent and sub-task fetches.

## 5. Classify status transitions

Apply these standard rules to each active issue:

| Current status | Observed GitHub and JIRA evidence | Proposed status | JIRA transition |
|---|---|---|---|
| `Confirmed` | Open non-draft PR exists | `Develop` | `Start Develop` |
| `Confirmed` | Only open draft PR exists | `Analysis` | `Start Analysis` |
| `Develop` | Engine and changed TC PRs pass team approval at current head, and only CI checks remain | `Handover` | `Accept the fix` |
| Any active status | All target and backport PRs merged, and EPIC open sub-task count is `0` | `Resolved` (`Fixed`) | Walk to `Resolved` |

When walking a merged issue to `Resolved` (`Fixed`), follow [Resolve an issue after its fix merges](../cubrid-jira/SKILL.md#resolve-an-issue-after-its-fix-merges).

## 6. Flag edge cases for user judgment

Never apply backward or ambiguous transitions automatically. List these cases separately for user choice:

- **`Develop` with draft PR only**: Ticket is `Develop`, and every open PR is still draft (`Need Analysis` to `Analysis` vs keep `Develop`).
- **`Analysis` with closed draft PRs**: Ticket is `Analysis`, and every linked draft PR closed without merge (`Stop work` to `Confirmed` vs keep `Analysis`).
- **Partial merge or open backports**: Main `develop` PR merged, while release backport or follow-up PRs remain open.
- **Merged EPIC with open sub-tasks**: Main EPIC PR merged, while one or more sub-tasks remain unresolved.
- **`Open` with linked PRs**: `CBRD` tickets in `Open` require triage before moving to `Confirmed`. Non-`CBRD` projects such as `CUBRIDQA` may offer `Start Progress` to `In Progress`.

## 7. Preview transitions and request confirmation

For each candidate ticket, inspect live transitions and preview the default dry-run:

```bash
cubrid-jira transition "$KEY" --output json
cubrid-jira transition "$KEY" --to "$TRANSITION_NAME" --output json
```

Pass `--resolution Fixed` for `Check in fix`. When resolving an issue via `Check in fix` (`--resolution Fixed`), check whether `fixVersions` is already set. If `fixVersions` is empty and JIRA requires `Fix Version/s` on `Check in fix`, include setting `Fix Version/s` to match `Target Version` (such as `guava` for `develop`) in the preview:

```bash
cubrid-jira update "$KEY" --field 'Fix Version/s=[{"name":"guava"}]' --output json
```

Present three sections to the user:

1. Standard transition table with ticket key, current status, proposed status, and PR or CI evidence.
2. Edge-case list asking which items to include.
3. Short summary of kept-as-is tickets.

Stop and wait for explicit user confirmation before any live JIRA write.

## 8. Execute approved transitions and check results

Run live writes only for user-approved tickets, adding `--yes`:

```bash
cubrid-jira update "$KEY" --field 'Fix Version/s=[{"name":"guava"}]' --yes --output json
cubrid-jira transition "$KEY" --to "$TRANSITION_NAME" --resolution Fixed --yes --output json
```

Omit `cubrid-jira update` when `Fix Version/s` is already set or not required. Omit `--resolution Fixed` for non-resolving transitions.

Check every changed issue with a live JQL read:

```bash
cubrid-jira jql "key in ($KEY_LIST)" --fields "key,status,resolution,fixVersions" --output json
```

Report a concise before-and-after table and list any skipped edge cases.
