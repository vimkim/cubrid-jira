# Manually runnable examples for alternate CUBRID JIRA servers.

# Query jira.cubrid.com through the server-aware, read-only comment API.
# Defaults to known issue RND-2851; override with: just example query-rnd-com RND-1234
# `search` cannot be used here because it currently always targets jira.cubrid.org.
query-rnd-com issue="RND-2851":
    uv run cubrid-jira comment-list {{issue}} --server http://jira.cubrid.com --limit 1 --output json
