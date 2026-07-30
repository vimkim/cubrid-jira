# Required fixes — review of tw-kang PRs #2/#3/#4 (2026-07-30)

Companion to [`review-2026-07-30-tw-kang-prs.md`](./review-2026-07-30-tw-kang-prs.md),
which carries the full failure scenarios. This file is the actionable list:
what must change, where, and how each fix is verified. All ten were confirmed
by adversarial verification and individually approved in the review session.

| # | Sev | Fix | Where |
|---|-----|-----|-------|
| F1 | HIGH | 401 on a read raises `JiraError(code=401)` and aborts the walk; callers exit 2 | `http.fetch_issue`, `walk`, `cli.cmd_search`, `cli._fetch_meta` |
| F2 | HIGH | Sanitize server-supplied attachment filenames to a basename; reject `..`/absolute/empty | `cli._dest_filename` (new) |
| F3 | med | Enforce `--max-bytes` on the actual stream, not the server-reported size; drop partial file | `http.download_file` (new) |
| F4 | med | Disambiguate duplicate attachment filenames with the attachment id | `cli._dest_filename` |
| F5 | med | Convert filesystem `OSError` to `JiraError`, delete partial files, keep the manifest contract | `http.download_file` |
| F6 | med | Drop the half-wired `--server` flag from `attachment` (reads are hard-wired to `JIRA_BASE`) | `cli` parser |
| F7 | med | Attachment download is a read: optional credentials, anonymous fallback, 401 → exit 2 | `cli.cmd_attachment`, `http.download_file` |
| F8 | low | Add `attachment` to the CLAUDE.md agent-contract table | `CLAUDE.md` |
| F9 | low | Default attachment dir honors `$CUBRID_JIRA_DIR` | `cache.resolve_attachment_dir` (new) |
| F10 | low | Memoize credential resolution (one `~/.netrc` parse per process) | `auth.resolve_credentials_optional` |

## Fix specifications

### F1 — fail fast on 401 in reads

- `fetch_issue` raises `JiraError(code=401)` on HTTP 401 instead of returning
  `{}`. Other HTTP errors keep the swallow-and-continue behavior — a 404/403
  on one related issue must not abort the whole walk, and carries no lockout
  risk.
- `walk.fetch_recursive` lets the error propagate (no catch), so **at most one
  failed auth attempt leaves the process per run**.
- `cmd_search`, `_fetch_meta`, and the legacy `cubrid-jira-fetch` entry point
  catch `JiraError` and exit via the shared HTTP→exit-code map (401 → 2).
- The map moves from `cli._exit_code_for_http` to `http.exit_code_for_http`
  so `walk.py` can use it without importing `cli`.
- Verified by: 401 search aborts after exactly 1 request, exit 2.

### F2 + F4 — safe, collision-free destination filenames

- New `cli._dest_filename(attachment, taken)`: takes the basename only
  (neutralizing `../`, absolute paths, and backslash separators), falls back
  to `attachment-<id>` for empty/`.`/`..` names, and appends `-<id>` before
  the suffix when the name was already taken in this run.
- The manifest reports the sanitized on-disk name, never the raw server value.
- Verified by: traversal/absolute names land inside `--out`; duplicate names
  produce two distinct files, both on disk.

### F3 + F5 + F7 — downloads move to the read layer

- `JiraClient.download` is replaced by a free function
  `http.download_file(url, dest, max_bytes=None)` in the read bucket:
  - auth via `resolve_credentials_optional` (anonymous fallback — F7);
  - `max_bytes` enforced on bytes actually received; exceeding it aborts,
    deletes the partial file, raises `JiraError` (F3);
  - `OSError` (disk full, permissions, bad name) is converted to `JiraError`
    and the partial file is removed (F5).
- `cmd_attachment` keeps the metadata `size` check only as a cheap pre-skip;
  the stream cap is authoritative. A 401 during download aborts the whole
  command with exit 2 (same lockout logic as F1) instead of retrying per
  attachment.
- Verified by: size-0 metadata + oversized stream → skipped, no partial file;
  OSError → `downloaded: false` entry, manifest still valid JSON; no-creds
  download sends no Authorization header.

### F6 — remove `--server` from attachment

The metadata read (`search_issues`) and credential resolution are hard-wired
to `JIRA_BASE`; honoring `--server` only for the download half returns wrong
answers silently. The flag is removed — passing it is now a loud argparse
error. Re-add only when the whole read path takes a base URL.

### F8 — CLAUDE.md contract table

`attachment` joins the read bucket row. The module-layout section gains
`download_file`.

### F9 — attachment dir resolution

New `cache.resolve_attachment_dir(key, cli_dir)`:
`--out` > `$CUBRID_JIRA_DIR/attachments/<KEY>` >
`~/.local/share/cubrid-jira/attachments/<KEY>`. Replaces the hardcoded
`cli._default_attachment_dir`.

### F10 — memoized credential resolution

`functools.lru_cache` on `resolve_credentials_optional` — credentials cannot
change mid-process; recursive walks stop re-parsing `~/.netrc` per issue.
Tests reset via `cache_clear()` in the autouse conftest fixture.
