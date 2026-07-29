# Authenticated Reads for Non-Public Projects

> Status: **FIXED (2026-07-24).** Implemented approach (A): reads attach basic
> auth when a credential resolves (env or `~/.netrc`) and stay anonymous
> otherwise. `search`/`jql`/`attachment`/`walk` deep fetch all authenticate now.
> This document is kept as the rationale + trap record.
>
> Originally logged 2026-07-23 while doing cubrid-agent work against the
> `CUBRIDQA` project. Re-confirmed 2026-07-24: recurred during a cubrid-agent
> `/code-review` — the Spec axis could not fetch `CUBRIDQA-1425`
> (`cubrid-jira search` → HTTP 401) and had to fall back to the PR body + ADRs.
> The `~/.netrc` credential was valid the whole time (authenticated
> `curl --netrc` for the same issue → HTTP 200); only the CLI read path skipped
> it. Second real incident — which is why it was prioritized and fixed.

## Summary

`cubrid-jira` reads (`search` one issue by key, `jql` list by query) use an
**unauthenticated** `GET`, while all writes go through the authenticated
`JiraClient` / `SessionClient`. This split is intentional and documented
(`CLAUDE.md`: `http.py … fetch_issue / search_issues (unauth GET)`), and it
works fine for projects whose issues are readable by anonymous users
(e.g. public `CBRD`).

It **fails for login-required projects** such as `CUBRIDQA`: the anonymous read
is rejected, so a valid credential in env / `~/.netrc` never gets a chance to be
used on the read path. The result is a confusing "credentials are fine but reads
don't work" state — because writes (which authenticate) do work with the same
credentials.

## Evidence (real incident)

Environment: no `CUBRID_JIRA_USER` / `CUBRID_JIRA_PASSWORD` env; a valid
`~/.netrc` entry for `jira.cubrid.org` (login `twkang`).

The credential is valid — an authenticated read via curl succeeds:

```text
curl --netrc http://jira.cubrid.org/rest/api/2/myself   → HTTP 200
  X-AUSERNAME: twkang
  X-Seraph-LoginReason: OK
```

But cubrid-jira reads of a `CUBRIDQA` issue go out anonymous and fail:

```text
cubrid-jira search CUBRIDQA-1425                  → HTTP 401 (fetch failed)
cubrid-jira jql "key = CUBRIDQA-1425"             → {"total": 0, "issues": []}
# JQL probe that reveals the anonymous identity:
jql "\"Epic Link\" = CUBRIDQA-1425"               → HTTP 400
  "Field 'Epic Link' does not exist or this field cannot be viewed by
   anonymous users."
```

The same issue and its sub-tasks read fine through an authenticated curl
(`GET /rest/api/2/issue/CUBRIDQA-1425?fields=…,subtasks`), confirming the only
missing piece is authentication on the read path.

## Root Cause

- `http.py` — `fetch_issue` and `search_issues` are unauthenticated `GET`
  helpers (see their docstrings and the `CLAUDE.md` module layout note).
- `auth.py` resolves credentials (env → `~/.netrc` → error), but today only the
  write path (`JiraClient` in `http.py`, `SessionClient` in `session.py`)
  consumes them. `basic_auth_header()` already exists in `http.py`.
- So credentials are resolved and usable, but reads never send them.

## What Needs Fixing

Let reads authenticate when credentials are available, without breaking the
anonymous fast-path for public projects.

Preferred approach: **if credentials resolve, attach basic auth to reads
directly** (do not send an anonymous request first). Fallbacks considered:

- (A) Always authenticate reads when `auth.resolve()` yields a credential;
  fall back to anonymous only when none is configured. *(recommended)*
- (B) Try anonymous, and on `401`/empty retry once **with** auth. Weaker:
  wastes a round-trip and edges toward the CAPTCHA footgun (see below).
- (C) Opt-in `--auth` flag / per-project config for non-public projects.
  Explicit but pushes the burden onto every caller.

Keep cache-first behavior for `search` and the exit-code contract
(`2=401, 3=403, 4=404, 5=400`) unchanged.

## Implementation Notes

- Respect the layering invariants enforced by `tests/test_layering.py`:
  `http.py` must not import `subprocess`; rendering stays in `markdown.py`.
  Reuse `basic_auth_header()` and `auth.resolve()` inside the read helpers.
- **CAPTCHA footgun (`CLAUDE.md` write-safety rule): on `401`, never retry.**
  Jira Server locks the account and triggers a CAPTCHA after a few failed
  basic-auth attempts. Prefer approach (A) — a single authenticated attempt —
  over (B)'s "anon then retry with auth", so a misconfigured password can't turn
  into a retry storm. A single failed auth read should exit `2` immediately,
  same as writes.
- An anonymous `GET` that returns `401` does **not** count as a failed
  basic-auth attempt (no creds were sent), so approach (A) does not increase
  lockout risk versus today.
- `walk.py` (recursive related-issue traversal) calls the read helpers — make
  sure its deep fetches authenticate too, or private sub-trees stay invisible.

## Tests To Add

Add mocked-server tests alongside the existing read tests:

- With creds present, `search`/`jql` send `Authorization: Basic …` and parse a
  private-project issue that an anonymous request would 401 on.
- With no creds configured, reads stay anonymous (public-project behavior
  unchanged).
- A `401` on an authenticated read exits `2` and does **not** retry.
- Cache-first behavior of `search` is unchanged (cache hit skips the network).
- `walk.py` deep fetch authenticates.
- A `live` test (`-m live`) reads a known `CUBRIDQA` issue and asserts a non-empty
  result when creds are available.

## Acceptance Criteria

- `cubrid-jira search CUBRIDQA-XXXX` and `jql` over `CUBRIDQA` return issues when
  a valid credential (env or `~/.netrc`) is present.
- Public `CBRD` reads keep working, cache-first behavior intact.
- No CAPTCHA lockout is introduced (single authenticated attempt, no retry on
  401).
- `tests/test_layering.py` still passes.

## Workaround (until fixed)

Read non-public issues with an authenticated curl using the same `~/.netrc`:

```bash
curl -sS --netrc \
  "http://jira.cubrid.org/rest/api/2/issue/CUBRIDQA-1425?fields=summary,description,status,subtasks"
```

Writes (`create`/`update`/`comment`/`transition`) are unaffected — they already
authenticate — so only the read path needs the workaround.
