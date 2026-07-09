# Markdown Comment Upload Confusion

## Summary

`cubrid-jira comment` and `cubrid-jira comment-update` previously uploaded the
`--body-file` contents almost literally as the Jira comment body. They did not
convert Markdown to Jira wiki markup.

This caused a real operator mistake: a Markdown report file was uploaded to
`CBRD-27029` with `cubrid-jira comment`, so Jira displayed raw Markdown syntax
instead of formatted headings, lists, code spans, and code blocks. The comment
had to be corrected later by converting the Markdown through the separate
`jira-md-comment`/`jira_md_upload.markdown_to_jira_body` path and then running
`cubrid-jira comment-update`.

The confusion is understandable because:

- `cubrid-jira search` prints Markdown.
- The examples use filenames like `note.md`.
- `--body-file` sounds like a Markdown upload flow to users working with local
  `.md` documents.
- The package already depended conceptually on `pandoc`, but only for
  Jira-wiki-to-Markdown rendering on reads.

## Resolution

`cubrid-jira` now treats `--body-file` and `--description-file` input as
Markdown by default for:

- `create`
- `update`
- `comment`
- `comment-update`

Use `--from jira` when the file/stdin content already contains raw Jira wiki
markup:

```bash
cubrid-jira comment CBRD-27029 --body-file note.md
cubrid-jira comment CBRD-27029 --body-file note.jira --from jira
cubrid-jira update CBRD-27029 --description-file issue.md
cubrid-jira update CBRD-27029 --description-file issue.jira --from jira
```

Missing or failing `pandoc` now fails the Markdown write command before any
request is sent. Raw Jira wiki input through `--from jira` does not require
`pandoc`.

## Original Behavior

Relevant code:

- `src/cubrid_jira/cli.py`
  - `cmd_comment`
  - `cmd_comment_update`
  - `build_comment_payload`
  - `build_comment_update_payload`
- `src/cubrid_jira/spacing.py`
  - `normalize_korean_jira_spacing`
- `src/cubrid_jira/markdown.py`
  - originally only converted Jira wiki markup to Markdown for fetched issue
    descriptions/comments.

The write path is:

```text
Path(args.body_file).read_text()
  -> normalize_korean_jira_spacing()
  -> {"body": body_text}
  -> POST /rest/api/2/issue/{key}/comment
```

For `comment-update`, stdin is supported:

```text
stdin or Path(args.body_file).read_text()
  -> normalize_korean_jira_spacing()
  -> {"body": body_text}
  -> PUT /rest/api/2/issue/{key}/comment/{comment_id}
```

No Markdown parser, `pandoc --from markdown --to jira`, or other conversion was
called in either write command.

The README said this explicitly:

```text
For --body-file and --description-file, write commands send raw Jira wiki text...
```

However, this is easy to miss during normal agent operation.

## Real Incident

The problematic upload command was:

```bash
cubrid-jira comment CBRD-27029 \
  --body-file /home/vimkim/gh/my-cubrid-docs/cbrd-27029/report-improved.md \
  --yes --output json
```

The result was:

```json
{"issue": "CBRD-27029", "comment_id": "4775159"}
```

Because `cubrid-jira comment` treated the file as raw Jira wiki text, Markdown
syntax such as `#`, `##`, backticks, fenced code blocks, and Markdown lists was
uploaded as-is.

The corrected update used the separate converter installed at
`/home/vimkim/temp/md-to-jira-uploader`:

```bash
/home/vimkim/.local/share/uv/tools/jira-md-upload/bin/python - <<'PY' \
  | cubrid-jira comment-update CBRD-27029 --id 4775159 --body-file - --yes --output json
from pathlib import Path
from jira_md_upload import markdown_to_jira_body

path = Path('/home/vimkim/gh/my-cubrid-docs/cbrd-27029/report-improved.md')
print(markdown_to_jira_body(path.read_text(encoding='utf-8')), end='')
PY
```

That produced:

```json
{"issue": "CBRD-27029", "comment_id": "4775159", "updated": true}
```

The converted body uses Jira wiki markup such as:

```text
h1. Title
h2. Section
{{inline code}}
{noformat}
code block
{noformat}
* bullet
# numbered item
```

## Existing Converter Outside This Repo

The separate editable tool lives at:

```text
/home/vimkim/temp/md-to-jira-uploader
```

Key functions:

- `jira_md_upload.markdown_to_jira_body(md_text)`
- `jira_md_upload.md_to_jira(md_text)`
- `jira_md_upload.sanitize_markdown(md_text)`
- `jira_md_upload.fix_korean_jira_inline_spacing(text)`
- `jira_md_upload.fix_jira_bold_code_nesting(text)`

Its conversion path is:

```text
Markdown
  -> Korean inline spacing cleanup in Markdown
  -> sanitize blank lines before headings/lists/fences
  -> pandoc --from markdown --to jira
  -> Jira bold/code nesting fix
  -> Korean inline spacing cleanup in Jira wiki markup
```

The CLI `jira-md-comment` posts a new comment with conversion by default and
has `--plain` to bypass conversion:

```bash
jira-md-comment CBRD-27029 report.md
jira-md-comment CBRD-27029 report.md --plain
```

But it is not integrated with `cubrid-jira`, and it does not provide the same
dry-run-default/write contract or `comment-update` behavior.

## What Needs Fixing

The upload behavior should be explicit and hard to misuse. Chosen
implementation:

1. Add a body input format option to write commands, defaulting to Markdown:

   ```bash
   cubrid-jira comment CBRD-27029 --body-file note.md
   cubrid-jira comment-update CBRD-27029 --id 123 --body-file note.md
   cubrid-jira create --project CBRD --type Task --summary "..." --description-file note.md
   cubrid-jira update CBRD-27029 --description-file note.md
   ```

2. Keep raw Jira wiki input available explicitly:

   ```bash
   cubrid-jira comment CBRD-27029 --body-file note.jira --from jira
   cubrid-jira update CBRD-27029 --description-file note.jira --from jira
   ```

3. Update help text and README so the default is unambiguous.

## Implementation Notes

- Put the Markdown-to-Jira conversion in a pure rendering layer, probably
  `src/cubrid_jira/markdown.py` or a new `src/cubrid_jira/formatting.py`.
- Do not add HTTP or credential handling to the converter.
- `src/cubrid_jira/http.py` must not import Markdown/pandoc code. Existing
  layering tests enforce this direction.
- The converter may shell out to `pandoc`, matching the existing
  Jira-wiki-to-Markdown read conversion.
- Missing `pandoc` must fail hard for default Markdown writes. Silent raw upload
  would repeat the original mistake.
- Raw Jira wiki uploads with `--from jira` must not require `pandoc`.
- Reuse or port the useful pieces from `/home/vimkim/temp/md-to-jira-uploader`
  instead of inventing a weaker conversion path.
- Existing `normalize_korean_jira_spacing()` is for Jira wiki markup, not raw
  Markdown. If converting Markdown, apply spacing at the correct stage.

## Tests To Add

Add tests around `main()`/fake server behavior, similar to existing write tests:

- Default `comment --body-file` posts Jira wiki markup converted from Markdown.
- Default `create/update --description-file` posts Jira wiki markup converted
  from Markdown.
- `comment-update --body-file -` converts stdin Markdown by default.
- `--from jira` keeps raw Jira wiki behavior and spacing fixes.
- Dry-run JSON shows the converted body for default Markdown input.
- Missing `pandoc` for Markdown input exits non-zero and does not send.
- README/help text states that Markdown is the default and `--from jira` is the
  raw Jira wiki opt-out.

Useful sample assertion:

```python
body_file.write_text("# Title\n\n`code`\n\n```text\nx\n```\n")
main(["comment", "CBRD-5", "--body-file", str(body_file), "--yes"])

payload = json.loads(fake_server.records[-1].body.decode())
assert "h1." in payload["body"]
assert "{{code}}" in payload["body"]
assert "{noformat}" in payload["body"]
assert "# Title" not in payload["body"]
assert "```" not in payload["body"]
```

## Acceptance Criteria

- A user can upload a local Markdown report as a formatted Jira comment using
  `cubrid-jira` without reaching for `jira-md-comment`.
- Raw Jira wiki behavior remains available and documented as `--from jira`.
- Dry-run output accurately shows the body that would be sent.
- The README and `--help` text make it clear whether a file is treated as Jira
  wiki markup or Markdown.
- Existing read behavior (`search`, `jql`, cache files) is unchanged.
