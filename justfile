# Run recipes from this checkout; pass arguments as quoted shell parameters.
set positional-arguments

mod example 'example.just'

# Show available commands.
default:
    @just --list

# ---- installation ---------------------------------------------------------- #

# Prepare the local development environment without changing the global command.
setup:
    uv sync --dev

# Set up development and install a global command that follows edits here.
install: setup
    uv tool install --force --editable "$PWD"

# Install an independent global copy (rerun to pick up source changes).
install-copy:
    uv tool install --force .

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
