# cubrid-jira justfile

default:
    @just --list

# ---- install / dev ---------------------------------------------------------- #

# Install the package + dev deps into .venv via uv.
install:
    uv sync --dev

# Run the unit + mocked-integration tests (live tests skipped).
test:
    uv run pytest

# Also run the live read-only smoke test against http://jira.cubrid.org.
test-live:
    uv run pytest -m live

# ---- read ------------------------------------------------------------------- #

# Cache-first lookup for one issue; prints markdown to stdout.
# Usage: just search CBRD-26463
search issue:
    uv run cubrid-jira search {{issue}}

# Force re-fetch a single issue (bypass cache).
# Usage: just search-force CBRD-26463
search-force issue:
    uv run cubrid-jira search {{issue}} --force

# ---- write (dry-run helpers — no --yes; never sends) ----------------------- #

# Dry-run: show the JSON for creating a Bug.
# Usage: just write-dry-create CBRD "the summary"
write-dry-create project summary:
    uv run cubrid-jira create --project {{project}} --type Bug --summary "{{summary}}"

# Dry-run: show the JSON for linking two issues with "Relates".
# Usage: just write-dry-link CBRD-1 CBRD-2
write-dry-link src dst:
    uv run cubrid-jira link {{src}} --type Relates --to {{dst}}

# Dry-run: show planned transition POST for an issue.
# Usage: just write-dry-transition CBRD-1 "In Progress"
write-dry-transition issue name:
    uv run cubrid-jira transition {{issue}} --to "{{name}}"
