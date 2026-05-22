# cubrid-jira-fetcher

Fetch CUBRID JIRA issues as markdown, with a cache-first lookup designed for AI agents and slash commands.

This package provides three command-line tools:

| Command | What it does |
|---|---|
| `cubrid-jira-search` | Look up **one** issue. Prints markdown to stdout. Uses a local cache; fetches from JIRA only on cache miss. **Use this from agents.** |
| `cubrid-jira-fetch` | Bulk-fetch an issue **and its related issues** into a directory of markdown files. |
| `cubrid-jira` | Parent command with read **and** write subcommands: `search`, `create`, `comment`, `link`, `transition`, `assign`. Writes share the same cache and credential layer. See [Writes](#writes-create-comment-link-transition-assign) below. |

Uses the JIRA REST API over plain HTTP, bypassing the redirect loop that occurs when tools force-upgrade to HTTPS.

![Demo](./demo.gif)

---

## Quick install (for AI agents)

**Use `uv tool install` — it is the right tool for this job.**

`uv tool install` installs each CLI into its own isolated environment and puts the binaries on `$PATH`, the same way `pipx` does but much faster. This is strictly better than `pip install -e .` or `pip install --user` for end users:

- ✅ No shared site-packages: the tool's deps can't break your other Python projects (and vice-versa).
- ✅ No editable / dev checkout required — `uv tool install` works straight from Git or PyPI.
- ✅ Self-healing `$PATH`: `uv tool update-shell` wires up the shim directory once.
- ✅ Easy uninstall: `uv tool uninstall cubrid-jira-fetcher`.
- ❌ `pip install -e .` is for **local development** only — it pollutes whatever Python environment happens to be active and its entry points silently break when that venv is deleted.

**One-liner** — pick whichever your environment has:

```sh
# With uv (recommended):
uv tool install git+https://github.com/<owner>/<repo>.git

# With pipx (equivalent behavior, slower):
pipx install git+https://github.com/<owner>/<repo>.git

# From a local clone:
cd cubrid-jira-fetcher && uv tool install . || pipx install .
```

After install, **both binaries are on `$PATH`**:

```sh
cubrid-jira-search --help
cubrid-jira-fetch --help
```

> Last-resort fallback (not recommended — no isolation):
> `pip install --user git+https://github.com/<owner>/<repo>.git`
>
> Do **not** use `pip install -e .` for installation; that mode is only appropriate when actively editing this repo's source.

---

## Prerequisites

- **Python 3.14+**
- **[pandoc](https://pandoc.org/)** — used to convert Jira wiki markup to markdown

Install pandoc:
```sh
brew install pandoc          # macOS / Linuxbrew
sudo apt install pandoc      # Debian / Ubuntu
sudo dnf install pandoc      # Fedora / RHEL
```

Optional dev tools:
- [uv](https://github.com/astral-sh/uv) — faster installs, used by the `justfile`
- [just](https://github.com/casey/just) — recipe runner

---

## Using `cubrid-jira-search` (the agent-friendly one)

Prints the issue's markdown to **stdout**. Progress messages go to stderr so piping stays clean.

```sh
cubrid-jira-search CBRD-26463
cubrid-jira-search http://jira.cubrid.org/browse/CBRD-26463
cubrid-jira-search CBRD-26463 --force         # bypass cache, re-fetch
cubrid-jira-search CBRD-26463 --no-recurse    # don't walk related issues on a miss
cubrid-jira-search CBRD-26463 --dir /tmp/jira # override cache directory
```

**What it does:**

1. Looks for `CBRD-26463*.md` in the cache directory.
2. **Cache hit** → prints it. No network.
3. **Cache miss** → fetches the issue (+ 1 level of related issues by default) into the cache, then prints it.
4. Exits non-zero if the fetch fails — callers can detect failure reliably.

### Cache directory

Resolved in this order (first match wins):

1. `--dir DIR` flag
2. `$CUBRID_JIRA_DIR` environment variable
3. `~/.local/share/cubrid-jira/issues/` (default)

**Recommended setup** — share the cache across all your tools and agents:

```sh
echo 'export CUBRID_JIRA_DIR="$HOME/.local/share/cubrid-jira/issues"' >> ~/.bashrc
```

### Piping into an agent

```sh
cubrid-jira-search CBRD-26463 2>/dev/null | your-agent --stdin
```

The Claude Code `/jira` skill calls `cubrid-jira-search` directly — no extra configuration beyond having the binary on `$PATH`.

### Options

| Flag | Default | Description |
|---|---|---|
| `-d`, `--dir DIR` | `$CUBRID_JIRA_DIR` or `~/.local/share/cubrid-jira/issues/` | Cache directory |
| `--no-recurse` | — | On cache miss, only fetch the requested issue (skip related) |
| `--force` | — | Ignore the cache and re-fetch from JIRA |

---

## Using `cubrid-jira-fetch` (bulk fetch)

Saves an issue and its related issues as markdown files in a directory.

```sh
cubrid-jira-fetch CBRD-26463
cubrid-jira-fetch http://jira.cubrid.org/browse/CBRD-26463
cubrid-jira-fetch CBRD-26463 --depth 3      # walk 3 levels of related issues
cubrid-jira-fetch CBRD-26463 --force        # re-download everything
cubrid-jira-fetch CBRD-26463 --json         # raw JSON instead of markdown
cubrid-jira-fetch CBRD-26463 -d my_issues   # custom output dir
```

### Output

Each issue becomes `{output-dir}/{KEY}.md` (or `.json` with `--json`):

```
related_issues/
├── CBRD-26463.md   ← the issue you requested
├── CBRD-26584.md   ← parent
├── CBRD-26433.md   ← related
└── CBRD-26521.md   ← related
```

Each markdown file contains: title and link, a metadata table (status, priority, type, assignee, reporter, resolution, components, versions, dates), the description and comments (converted via pandoc), and a list of related issues.

### Options

| Flag | Default | Description |
|---|---|---|
| `-d`, `--output-dir` | `related_issues/` | Directory to save files into |
| `--depth N` | `1` | Levels of related issues to follow |
| `--no-recurse` | — | Only fetch the given issue (same as `--depth 0`) |
| `--force` | — | Re-download and overwrite already-saved files |
| `--json` | — | Save raw JSON instead of markdown |

---

## Writes (`create`, `comment`, `link`, `transition`, `assign`)

The `cubrid-jira` parent command extends the read-only fetcher with write operations against the same JIRA Server. **All write subcommands are dry-run by default; you must pass `--yes` to actually send the request.** This is intentional — there is no staging JIRA, only production.

### ⚠️ Security & lockout warnings

- **Cleartext credentials.** `jira.cubrid.org` only speaks HTTP; basic-auth headers travel unencrypted. Use a network you trust and a dedicated JIRA password, not your SSO password.
- **CAPTCHA lockout.** Jira Server 7.7.1 locks an account and requires a web-UI CAPTCHA after a small number of failed basic-auth attempts. `cubrid-jira` **never retries on HTTP 401**; if you see the 401 error, fix your credentials before retrying — repeated failures will lock you out of the REST API until you log in via the browser and clear the CAPTCHA.

### Credentials

Resolved in this order (first hit wins):

1. `CUBRID_JIRA_USER` + `CUBRID_JIRA_PASSWORD` environment variables
2. `~/.netrc` entry for `jira.cubrid.org`
3. Hard error with an instructive message (no interactive prompt — these tools are meant to run from agents)

Example `~/.netrc`:

```
machine jira.cubrid.org
  login your-jira-username
  password your-jira-password
```

```sh
chmod 600 ~/.netrc
```

### Subcommands at a glance

```sh
# Always dry-run by default — print the planned request and exit.
cubrid-jira create     --project CBRD --type Bug --summary "..." \
                       [--description-file path] [--priority Major] [--assignee user] \
                       [--label l1 --label l2] [--component sql] \
                       [--link-relates CBRD-Y] [--link-blocks CBRD-Z]
cubrid-jira comment    CBRD-XXXXX --body-file note.md
cubrid-jira link       CBRD-A --type Relates --to CBRD-B   # also Blocks | Cloners | Duplicate
cubrid-jira transition CBRD-A [--to "In Progress"]         # omit --to to list available transitions
cubrid-jira assign     CBRD-A --to <username>              # --to "" to unassign

# Add --yes to actually perform the write.
cubrid-jira <subcommand> ... --yes
```

Global flags on every write subcommand:

| Flag | Default | Description |
|---|---|---|
| `--dry-run` | (default behavior) | Print the resolved URL, masked headers, and JSON body. Don't send. |
| `--yes` | off | Required to actually send the request. |
| `--server URL` | `http://jira.cubrid.org` | JIRA base URL. |
| `-d`, `--dir DIR` | shared cache (see above) | Cache directory used for the post-write cache update. |

### Cache interaction

- `create`: on success, the new issue is fetched and saved into the cache — `cubrid-jira search NEW-KEY` immediately hits the cache.
- `comment`, `link`, `transition`, `assign`: the cached markdown for the affected issue key(s) is **deleted**, so the next read re-fetches fresh data. (For `link`, both sides of the link are invalidated.)

### Worked example — create a bug and relate it to `CBRD-26517`

```sh
# 1) Dry-run: review the planned request first.
cubrid-jira create \
    --project CBRD \
    --type Bug \
    --summary "OOS: heap_record_replace crashes when …" \
    --priority Major \
    --description-file ./bug-notes.md \
    --link-relates CBRD-26517

# 2) Same command, with --yes, actually creates the issue.
cubrid-jira create \
    --project CBRD \
    --type Bug \
    --summary "OOS: heap_record_replace crashes when …" \
    --priority Major \
    --description-file ./bug-notes.md \
    --link-relates CBRD-26517 \
    --yes
# → "Created CBRD-NNNNN: http://jira.cubrid.org/browse/CBRD-NNNNN"
# → ./~/.local/share/cubrid-jira/issues/CBRD-NNNNN.md is now cached
```

### Errors

- **401 — Auth failed:** hard exit, no retry. Reset CAPTCHA in the web UI and re-check credentials.
- **403 — Forbidden:** authenticated but missing permission for the resource.
- **404:** check the issue key.
- **400:** the server's `errorMessages` / `errors` payload is printed verbatim (usually field validation).
- Transient network / `5xx`: retried once with a short backoff.

---

## Caching behavior

Both tools share the same on-disk cache. Already-saved files are skipped on subsequent runs; related issues are still traversed so a later `--depth 2` run correctly extends a prior `--depth 1` run. Use `--force` to re-download.

A markdown file written by `cubrid-jira-fetch` is served immediately by `cubrid-jira-search` (and vice-versa) when both point at the same directory.

---

## Development

```sh
git clone https://github.com/<owner>/<repo>.git
cd cubrid-jira-fetcher
uv sync              # creates .venv and installs entry points
uv run cubrid-jira-search CBRD-26463
```

Or with `just`:

```sh
just install         # uv sync
just search CBRD-26463
just fetch CBRD-26463
just fetch-deep CBRD-26463 3
just force CBRD-26463
just json CBRD-26463
```

---

## Troubleshooting

- **`command not found: cubrid-jira-search`** — the install dir isn't on `$PATH`. With `uv tool install`, run `uv tool update-shell`. With `pipx`, run `pipx ensurepath`. Then restart the shell.
- **`pandoc: command not found`** — install pandoc (see Prerequisites).
- **Redirect loop / HTTPS errors** — JIRA responses are expected over plain HTTP; do not force HTTPS at the proxy level.
- **Stale cache** — `cubrid-jira-search CBRD-XXXXX --force` to re-fetch a single issue; delete the cache directory to reset everything.
