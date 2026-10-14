# Issue Tracker: GitHub

Track development issues and specs for `cubrid-jira` in `vimkim/cubrid-jira` on GitHub. Use `gh` from this checkout, or pass `--repo vimkim/cubrid-jira` from another directory.

## Issue operations

- Create: `gh issue create --title "<title>" --body-file <path>`.
- Read the body and comments: `gh issue view <number> --json number,title,body,labels,comments`.
- List: `gh issue list --state open --json number,title,body,labels,comments`; add label or state filters as needed.
- Replace the body: `gh issue edit <number> --body-file <path>`.
- Comment: `gh issue comment <number> --body-file <path>`.
- Apply or remove labels: `gh issue edit <number> --add-label <label>` or `--remove-label <label>`. See `triage-labels.md` for role names.
- Close: `gh issue close <number>`.

Store multiline bodies in a file and use `--body-file`. Use terminal commands and printed URLs for this remote server.

## Skill conventions

When a skill says "publish to the issue tracker", create a GitHub issue. When it says "fetch the relevant ticket", read the issue body and comments. A spec or handoff path provided by the user is also a valid implementation or review source.

## Parent issues and blocking edges

- Create a child: add `--parent <parent-number>` to `gh issue create`.
- Attach an existing child: `gh issue edit <parent-number> --add-sub-issue <child-number>`.
- Declare blockers at creation: add `--blocked-by <blocker-number>,<blocker-number>`.
- Add a blocker later: `gh issue edit <child-number> --add-blocked-by <blocker-number>`.

Check installed `gh` help before using these flags on another host. If the server cannot represent a parent or blocker, record `Part of #<parent>` or `Blocked by: #<blocker>` in the child body. An issue is ready when all its blockers are closed.

## Wayfinding operations

A map is one issue holding Notes, Decisions-so-far, and Fog, labelled `wayfinder:map`. Each decision ticket is a child issue labelled `wayfinder:<type>` (`research`, `prototype`, `grilling`, or `task`).

- Frontier: inspect the map's children; select an open, unassigned issue whose blockers are closed, in map order.
- Claim: `gh issue edit <number> --add-assignee @me` before work.
- Resolve: append the answer with `gh issue comment --body-file`, close the child, then update the map with a brief decision and a link to the child.

## Pull requests as a triage surface

**PRs as a request surface: no.**

When this flag is enabled, use `gh pr view <number> --comments` and `gh pr diff <number>` to read PRs, and the corresponding `gh pr` commands for comments, labels, and closure. GitHub shares issue and PR numbers; resolve an ambiguous reference before acting.
