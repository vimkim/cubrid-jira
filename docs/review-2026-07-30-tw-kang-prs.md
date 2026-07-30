# Code Review Report — tw-kang PRs #2, #3, #4

- **Date:** 2026-07-30
- **Scope:** `git diff 9b6618d..HEAD` — the three recently merged PRs from tw-kang
  - **PR #2** — `feat/attachment-subcommand` (merge `43234c7`)
  - **PR #3** — `feat/authenticated-reads` (merge `e456553`)
  - **PR #4** — `fix/pandoc-read-fallback` (merge `0330ca4`)
- **Method:** 8 finder angles → ~30 candidates → dedup → 12 adversarially verified → **10 confirmed, 2 refuted**

## Verdict

All three PRs work as advertised and were reasonable merges, but they are not
defect-free. Two findings are high priority (F1, F2). PR #4 came out clean.

| PR | Findings |
|----|----------|
| #3 authenticated reads | F1 (high) |
| #2 attachment subcommand | F2 (high), F3–F7, F9 |
| #2/#3 shared / repo-level | F8 (docs), F10 (perf) |
| #4 pandoc fallback | none |

---

## Confirmed findings

### F1 (HIGH) — 401s swallowed in `fetch_issue` → retry loop risks CAPTCHA lockout

**`src/cubrid_jira/http.py:86`** · correctness/security · introduced by interaction with PR #3

`fetch_issue` catches auth failures, prints to stderr, and returns `{}` instead
of raising. Before PR #3 reads were anonymous so this was harmless. Now that
reads send basic-auth credentials when they resolve, the recursive walk
(`walk.fetch_recursive`, invoked by `cubrid-jira search` at `cli.py:388` with
default depth 1) sends the **same bad credential once per related issue**:

- Stale password in `~/.netrc` + `cubrid-jira search CBRD-1` → several failed
  basic-auth attempts in a single run. Jira Server locks the account and
  raises a CAPTCHA after a few failures — exactly the footgun this repo's
  CLAUDE.md rule ("On HTTP 401, never retry") and PR #3's own design doc
  (`docs/authenticated-reads-for-nonpublic-projects.md`: "A single failed auth
  read should exit 2 immediately") forbid.
- Exit-code contract misreport: `cmd_search` exits **1** (`cli.py:391`) and
  `_fetch_meta` exits **4** ("not found", `cli.py:985`) on what is really a
  401. Only `search_issues` correctly raises `JiraError(code=401)`.

**Fix direction:** make `fetch_issue` raise (or return a typed error) on 401,
abort the walk on first auth failure, and exit 2.

### F2 (HIGH) — Path traversal via server-supplied attachment filename

**`src/cubrid_jira/cli.py:507`** · security · PR #2

`dest = out_dir / name` uses the attachment's `filename` field verbatim.

- A filename of `../../../home/user/.bashrc` escapes `out_dir`.
- An absolute filename is worse: `Path(out_dir) / "/etc/x"` evaluates to
  `/etc/x` (pathlib semantics — an absolute right operand replaces the left).
- `client.download()` then opens `dest` with `"wb"` and overwrites the target
  with server-controlled bytes.

Anyone able to attach files to a Jira issue can write arbitrary files as
whoever runs `cubrid-jira attachment KEY`.

**Fix direction:** sanitize to basename, reject `/`, `\`, `..`, and empty
names; verify `dest.resolve()` stays under `out_dir.resolve()`.

### F3 — `--max-bytes` trusts server-reported size; no cap while streaming

**`src/cubrid_jira/cli.py:504`** · correctness · PR #2

The gate is `size = a.get("size") or 0` → `if size > args.max_bytes: skip`.
If the metadata omits `size` (or reports 0/wrong), the gate passes, and
`JiraClient.download` (`http.py:249–256`) streams to disk with **no byte
limit** — `written` is counted but never compared. A multi-GB core dump gets
pulled despite `--max-bytes`, defeating its documented purpose.

**Fix direction:** enforce the cap inside the download loop (abort + delete
partial file when `written > max_bytes`), treat missing size as "unknown, cap
at stream time".

### F4 — Duplicate attachment filenames silently clobber each other

**`src/cubrid_jira/cli.py:507`** · correctness · PR #2

Jira attachments are id-keyed; two attachments named `screenshot.png` on one
issue are legal and common (before/after screenshots on CBRD tickets). Both
loop iterations compute the same `dest`; the second download overwrites the
first, while the manifest lists **both** entries as `downloaded: true` at the
identical path. Silent data loss with a manifest that claims success.

**Fix direction:** disambiguate by attachment id (`name-<id>.ext`) or suffix
on collision.

### F5 — Uncaught `OSError` in `download()` breaks the JSON contract, leaves partial file

**`src/cubrid_jira/http.py:250`** · correctness · PR #2

`download()`'s `try` only catches urllib `HTTPError`/`URLError`;
`cmd_attachment` (`cli.py:512`) only catches `JiraError`. Disk full, an
unwritable `out_dir`, or a filename invalid for the local filesystem raises
`OSError` from `open()`/`write()` → unhandled traceback, **no manifest at all**
(breaking the `--output json` one-JSON-object contract), and a partially
written `dest` left on disk.

**Fix direction:** wrap the filesystem I/O, convert to `JiraError` (or record
`downloaded: false, error: ...` in the manifest), remove partial files.

### F6 — `--server` is silently half-ignored

**`src/cubrid_jira/cli.py:466`** · correctness · PR #2

`attachment` accepts `--server`, but the metadata read goes through
`search_issues`, which is hard-wired to `JIRA_BASE`
(`http://jira.cubrid.org`), and `_read_headers` resolves netrc credentials for
`jira.cubrid.org` — while the download client authenticates against the
`--server` host. `cubrid-jira attachment PROJ-1 --server http://other.example`
returns issues/attachments from the **wrong server** (or none) with no error.

**Fix direction:** thread the server through metadata fetch + credential
resolution, or drop the flag until reads support it.

### F7 — Attachment download hard-requires credentials for public issues

**`src/cubrid_jira/cli.py:491`** · correctness · PR #2

The metadata read falls back to anonymous access (that's the read bucket's
documented behavior), but `_make_client` → `resolve_credentials` does
`sys.exit(1)` when nothing resolves. On a machine with no env creds and no
`~/.netrc`, `cubrid-jira attachment CBRD-1` on a **public** issue succeeds at
the read, then dies with "No CUBRID JIRA credentials found" — generic exit 1
after a successful read, contradicting the anonymous-read fallback.

**Fix direction:** use `resolve_credentials_optional` for the download path;
attempt anonymous download, fail with a clear message only on 401/403.

### F8 — CLAUDE.md agent-contract table not updated for `attachment`

**`CLAUDE.md:5`** · documentation · PR #2

The contract table still reads
`read  search (one issue by key, cache-first) | jql (list by query, live)`.
The file calls itself "agent contract for cubrid-jira", so agents driving the
CLI from CLAUDE.md never learn `attachment` exists; README and CLAUDE.md now
disagree about the canonical subcommand set.

### F9 — Attachment dir ignores `$CUBRID_JIRA_DIR`

**`src/cubrid_jira/cli.py:437`** · simplification · PR #2

`_default_attachment_dir` hardcodes
`~/.local/share/cubrid-jira/attachments/<KEY>`, duplicating dir-resolution
logic instead of reusing `cache.resolve_cache_dir` (`cache.py:17–23`), which
honors the `$CUBRID_JIRA_DIR` override documented in CLAUDE.md. Tests already
work around this by always passing `--out` (both download tests in
`tests/test_attachment.py` do so to avoid writing into the runner's real home).

### F10 — `~/.netrc` re-parsed on every read GET

**`src/cubrid_jira/http.py:66`** · efficiency · PR #3

`_read_headers` re-runs `resolve_credentials_optional` — including a full
`netrc.netrc()` parse (`auth.py:39`) — on every read. `walk.fetch_recursive`
over a large issue tree does O(n) file I/O for a value that cannot change
mid-process.

**Fix direction:** memoize per process, e.g. `functools.lru_cache` on
`resolve_credentials_optional`.

---

## Refuted candidates (for the record)

1. **"`--dry-run` ignored by attachment download."** Refuted: the attachment
   parser never receives `--dry-run`/`--yes` (those come only from
   `_add_write_globals`), and `JiraClient` documents that GET requests always
   execute even in dry-run mode. Downloads are reads by design.
2. **"`filename` may be `None` in `--list`."** Refuted: the Jira v2 attachment
   schema always carries `filename`.

## Suggested fix order

1. **F1** — fail fast on 401 in reads (lockout + exit-code contract).
2. **F2** — sanitize attachment filenames (arbitrary file write).
3. **F3, F4, F5** — harden the download loop (stream cap, collision suffix, OSError handling).
4. **F6, F7** — `--server` consistency and anonymous-download fallback.
5. **F8, F9, F10** — docs sync, dir resolution reuse, credential memoization.
