# cubrid-jira

CLI client for the CUBRID Jira Server (jira.cubrid.org). Reads are cache-first
and work anonymously on public projects; writes require credentials and are
dry-run by default.

## Language

**Failed auth attempt**:
One HTTP request that carried basic-auth credentials and got a 401 back. The
lockout budget is counted per account, cumulatively across requests — not per
command or per request.
_Avoid_: retry, login failure (ambiguous about the counting unit)

**Lockout**:
Jira Server disabling basic-auth for an account after a few failed auth
attempts, requiring a CAPTCHA reset via the web UI. The reason 401 must abort
a run immediately (exit 2), never be swallowed or retried.

**Authenticated read**:
A GET that attaches basic auth when a credential resolves (env → netrc),
single attempt, no anonymous probe first. Falls back to anonymous when no
credential resolves — public projects must keep working without credentials.
_Avoid_: login read, private read

**Read bucket**:
Subcommands that only GET from the server (search, jql, attachment).
Credentials are optional (anonymous fallback) and dry-run does not apply —
GETs always execute. Local disk writes (cache, downloads) stay in this bucket
because they are reversible and leave no trace on the server.
_Avoid_: query commands, safe commands

**Write bucket**:
Subcommands that change server state (create, comment, transition, convert,
reparent, …). Credentials are required and every send is gated behind
`--yes`; without it the command is a dry-run no-op. What the gate protects is
the server, not the local machine.
_Avoid_: mutating commands, dangerous commands

**Round-trip-safe body**:
A Markdown issue body that can pass through the Jira→Markdown read conversion
and the Markdown→Jira write conversion without losing table cell boundaries,
cell content, or verbatim code-block content.
_Avoid_: safe Markdown (does not name which boundary is safe)

**Simple table**:
A Markdown table whose columns are inferred from whitespace and a dashed ruler.
Write commands reject this form because editing a cell past its ruler can
silently change the table.
_Avoid_: aligned table, whitespace table
