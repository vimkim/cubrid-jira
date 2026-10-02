# Manually runnable examples for alternate CUBRID JIRA servers.

# Query jira.cubrid.com through the server-aware search API.
# Defaults to known issue RND-2851; override with: just example query-rnd-com RND-1234
query-rnd-com issue="RND-2851":
    uv run cubrid-jira search {{quote(issue)}} --server http://jira.cubrid.com --no-recurse
