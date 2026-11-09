# Set up cubrid-jira

This guide installs `cubrid-jira`, gives it credentials, picks the JIRA server,
and fixes common setup errors.

## Install

`cubrid-jira` needs Python 3.14 or later. Install it as a tool so it gets its
own environment and lands on your `PATH`:

```sh
uv tool install git+https://github.com/vimkim/cubrid-jira.git
```

If you use `pipx` instead of `uv`:

```sh
pipx install git+https://github.com/vimkim/cubrid-jira.git
```

To upgrade, run the `uv tool install` command again with `--force`.

The package installs four commands:

| Command | Status |
|---|---|
| `cubrid-jira` | The main CLI. |
| `cubrid-jira-kanban` | Your open tickets, grouped by status. |
| `cubrid-jira-search` | Deprecated. Use `cubrid-jira search`. |
| `cubrid-jira-fetch` | Deprecated bulk fetcher from before the 1.0 rename. |

To install from a local checkout, see [Development](development.md).

## Install pandoc

`cubrid-jira` uses [pandoc](https://pandoc.org/) to turn Jira wiki markup into
Markdown on reads, and Markdown into Jira wiki markup on writes. You need a
pandoc that has the `jira` format, which arrived in 2.9.1. Use 2.19 or later:
the 2.9.1 writer drops the header row of Markdown tables.

```sh
brew install pandoc
sudo apt install pandoc
sudo dnf install pandoc
```

Distribution packages can be much older. RHEL 8 ships 2.0.6, which has no
`jira` format. Check the format, not the version string:

```sh
pandoc --list-input-formats | grep -qx jira && echo ok
```

Without a suitable pandoc, reads still work and print raw Jira wiki markup with
a warning. Markdown write bodies fail before anything is sent.

## Give it credentials

Reads work anonymously on public projects. Writes, their dry runs,
`cubrid-jira-kanban`, and reads of login-only projects such as `CUBRIDQA` need
an account. The CLI looks
for credentials in this order:

1. The `CUBRID_JIRA_USER` and `CUBRID_JIRA_PASSWORD` environment variables.
2. A `~/.netrc` entry for the server's hostname.

It never prompts. If a command needs credentials and finds none, it exits with
an error that says what to set.

A `~/.netrc` entry looks like this:

```text
machine jira.cubrid.org
  login your-jira-username
  password your-jira-password
```

Make the file readable only by you:

```sh
chmod 600 ~/.netrc
```

If your accounts differ between servers, add one `machine` entry per hostname.

> [!WARNING]
> JIRA Server locks an account after a few failed logins and then demands a
> CAPTCHA in the web UI. `cubrid-jira` exits on the first HTTP 401 and never
> retries. If you see `Error: Auth failed (HTTP 401)`, log in at
> `https://jira.cubrid.org` in a browser, solve the CAPTCHA, fix your
> credentials, and then run the command once more.

## Pick the server

CUBRID runs two separate JIRA installations. The CLI does not guess the server
from the issue key.

| Server | Typical projects | How to select it |
|---|---|---|
| `https://jira.cubrid.org` | `CBRD`, `CUBRIDQA` | The default. |
| `http://jira.cubrid.com` | `RND` | Add `--server http://jira.cubrid.com`, or pass a full `jira.cubrid.com` browse URL as the issue. |

```sh
cubrid-jira search RND-2851 --server http://jira.cubrid.com
cubrid-jira search http://jira.cubrid.com/browse/RND-2851
```

If `--server` and a browse URL name different servers, the command fails before
it sends a request.

`RND` issues are not visible anonymously, so reads on `jira.cubrid.com` need an
account there too. Add a `machine jira.cubrid.com` entry to `~/.netrc`.

> [!CAUTION]
> `jira.cubrid.com` serves only plain HTTP, so basic-auth credentials sent to it
> travel unencrypted. Use it from a trusted network with a password that you
> use only for JIRA.

## Where files go

`cubrid-jira` keeps issue Markdown in a cache directory. The first match wins:

1. `--dir DIR` (or `-d DIR`) on the command.
2. The `CUBRID_JIRA_DIR` environment variable.
3. `~/.local/share/cubrid-jira/issues/`.

To keep the cache in one place across shells, add this to your shell profile:

```sh
export CUBRID_JIRA_DIR="$HOME/.local/share/cubrid-jira/issues"
```

Files for `jira.cubrid.org` sit directly in the cache directory, such as
`CBRD-26463.md`. Files for other servers sit in a hostname directory, such as
`jira.cubrid.com/RND-2851.md`, so the same key on two servers cannot collide.

| File | Location |
|---|---|
| Issue Markdown | `<cache>/<KEY>.md`, or `<cache>/<host>/<KEY>.md` for other servers. |
| Field-name map for `--field` | `<cache>/field-map.json`, or `<cache>/<host>/field-map.json`. |
| Attachments | `$CUBRID_JIRA_DIR/attachments/<KEY>/` when the variable is set, else `~/.local/share/cubrid-jira/attachments/<KEY>/`. Other servers add a `<host>/` directory before the key. `--out DIR` overrides it. |

## Fix setup problems

**`command not found: cubrid-jira`.** The tool directory is not on your `PATH`.
Run `uv tool update-shell` (or `pipx ensurepath`) and open a new shell.

**`pandoc: command not found`.** Install pandoc as described in
[Install pandoc](#install-pandoc). Until then, reads print raw Jira wiki markup
and Markdown writes fail. `--from jira` works only when your file already holds
Jira wiki markup.

**`Warning: pandoc cannot convert Jira wiki markup`, and bodies show `h2.` or
`||table||`.** Your pandoc has no `jira` reader. The read is complete but not
converted. Upgrade pandoc.

**`Error: Auth failed (HTTP 401)`.** Do not retry. Follow the steps in the
warning under [Give it credentials](#give-it-credentials).

**`Error: POST ... was redirected to ...`.** The server answered with a
redirect, so the CLI sent nothing. Rerun with the `--server` value that the
error prints.

**You need an issue while offline.** Read the cached copy with
`cubrid-jira search CBRD-26463 --cache-only`. A normal `search` always fetches
live.

**`DeprecationWarning` when you import `cubrid_jira_fetcher`.** The module was
renamed. Import `cubrid_jira` instead.
