# Run recipes from this checkout; pass arguments as quoted shell parameters.
set positional-arguments

mod example 'example.just'

# Show available commands.
default:
    @just --list

# ---- installation ---------------------------------------------------------- #

# Refresh all user commands and bundled skills from local source for daily-update-cubrid.
sync:
    uv tool install --reinstall .
    @"$(uv tool dir --bin)/cubrid-jira" --help >/dev/null
    @"$(uv tool dir --bin)/cubrid-jira-kanban" --help >/dev/null
    "{{just_executable()}}" install-skills

# Prepare the local development environment without changing the global command.
setup:
    uv sync --dev

# Set up development and install a global command that follows edits here, plus bundled skills.
install: setup
    uv tool install --force --editable "$PWD"
    "{{just_executable()}}" install-skills

# Install an independent global copy and bundled skills (rerun to pick up source changes).
install-copy:
    uv tool install --force .
    "{{just_executable()}}" install-skills

# Install bundled skills (copy to ~/.agents/skills and link for Claude, Codex, and Antigravity).
install-skills:
    #!/usr/bin/env bash
    set -euo pipefail
    skill_root="{{justfile_directory()}}/skills"
    agents_dir="$HOME/.agents/skills"
    claude_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
    codex_dir="${CODEX_HOME:-$HOME/.codex}/skills"
    agy_dir="$HOME/.gemini/config/skills"
    mkdir -p "$agents_dir" "$claude_dir" "$codex_dir" "$agy_dir"

    link_agent() {
        local target="$1"
        local dir="$2"
        local name="$3"
        local link_path="$dir/$name"
        if [ -L "$link_path" ] || [ -e "$link_path" ]; then
            rm -rf "${dir:?}/${name:?}"
        fi
        ln -s "$target" "$link_path"
    }

    for src in "$skill_root"/*; do
        [ -d "$src" ] && [ -f "$src/SKILL.md" ] || continue
        name="$(basename "$src")"
        staging="$(mktemp -d "${agents_dir}/.${name}.XXXXXX")"
        cp -R "$src/." "$staging/"
        find "$staging" -type f -name '*.pyc' -delete
        find "$staging" -type d -name '__pycache__' -empty -delete
        if [ -L "$agents_dir/$name" ] || [ -e "$agents_dir/$name" ]; then
            rm -rf "${agents_dir:?}/${name:?}"
        fi
        mv "$staging" "$agents_dir/$name"
        if [ "$claude_dir" = "$HOME/.claude/skills" ]; then
            link_agent "../../.agents/skills/$name" "$claude_dir" "$name"
        else
            link_agent "$agents_dir/$name" "$claude_dir" "$name"
        fi
        link_agent "$agents_dir/$name" "$codex_dir" "$name"
        link_agent "$agents_dir/$name" "$agy_dir" "$name"
        printf 'Installed skill %s -> %s\n' "$name" "$agents_dir/$name"
    done

# After moving this checkout, repair local launchers and the global editable install.
repair-install:
    uv sync --dev --reinstall
    uv tool install --force --editable "$PWD"

# Show the checkout, local import location, and whether the global command starts.
doctor:
    @pwd
    uv run python -c 'import sys, cubrid_jira; print("Local Python:", sys.executable); print("Local package:", cubrid_jira.__file__)'
    @command -v cubrid-jira
    cubrid-jira --help

# ---- development ----------------------------------------------------------- #

# Run non-live tests; accepts pytest options: just test -k server.
test *args:
    uv run python -m pytest "$@"

# Run only live read-only tests against JIRA; requires network access.
test-live *args:
    uv run python -m pytest -m live "$@"

# Re-render the markup oracle cases with Jira's renderer (needs network; renders only what is missing).
render-snapshot *args:
    uv run python tests/render_snapshot.py "$@"

# Run tests and check the local CLI starts (no live JIRA requests).
check: test
    uv run cubrid-jira --help

# Writes retain the CLI's dry-run default; --yes explicitly sends them.
# Run any CLI command from this checkout: just run search CBRD-1 --no-recurse.
run *args:
    uv run cubrid-jira "$@"

# ---- reads ----------------------------------------------------------------- #

# Fetch an issue; accepts CLI options: just search CBRD-1 --no-recurse.
search issue *args:
    uv run cubrid-jira search "$@"

# Compatibility shortcut; ordinary search already fetches live data.
search-force issue *args:
    uv run cubrid-jira search --force "$@"

# Read an issue from the local cache without a network request.
search-cached issue:
    uv run cubrid-jira search --cache-only "$1"

# Query issues: just jql 'project = CBRD ORDER BY updated DESC' --max 10.
jql query *args:
    uv run cubrid-jira jql "$@"

# List attachment metadata without downloading files.
attachments issue:
    uv run cubrid-jira attachment --list "$1"

# ---- write previews (never pass --yes) -------------------------------------- #

# Preview a Bug creation: just write-dry-create CBRD 'The summary'.
write-dry-create project summary:
    uv run cubrid-jira create --project "$1" --type Bug --summary "$2"

# Preview a Relates link between two issues.
write-dry-link src dst:
    uv run cubrid-jira link "$1" --type Relates --to "$2"

# Preview a transition: just write-dry-transition CBRD-1 'In Progress'.
write-dry-transition issue name:
    uv run cubrid-jira transition "$1" --to "$2"
