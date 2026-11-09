# Recipes

Each recipe previews the change first and sends it only in the last step, with
`--yes`. Replace `CBRD-XXXXX` and the other placeholder keys with real ones.
Every write needs credentials. See [Set up cubrid-jira](setup.md).

## Create a bug linked to another issue

1. Write the description in Markdown, for example in `bug.md`.
2. Preview the requests:

   ```sh
   cubrid-jira create --project CBRD --type Bug \
     --summary "csql crashes on an empty table" \
     --description-file bug.md --priority Major \
     --link-relates CBRD-XXXXX --output json
   ```

   The plan has three requests: `POST /rest/api/2/issue`, a `PUT` that sets the
   priority, and `POST /rest/api/2/issueLink`.

3. Send them and keep the new key:

   ```sh
   KEY=$(cubrid-jira create --project CBRD --type Bug \
     --summary "csql crashes on an empty table" \
     --description-file bug.md --priority Major \
     --link-relates CBRD-XXXXX --yes --output json | jq -r .key)
   ```

4. Read the new issue:

   ```sh
   cubrid-jira search "$KEY"
   ```

If the priority or link request fails, the issue still exists. stderr prints
the new key and the command that finishes the job. See
[`create`](commands.md#create).

## Create a sub-task

`CBRD` requires the `QA Scenario` field, a single-select field that takes a JSON
option object.

```sh
cubrid-jira create --project CBRD --type Sub-task --parent CBRD-XXXXX \
  --summary "Investigate page-buffer ownership" \
  --field 'QA Scenario={"value":"Required"}' --output json
```

Check the plan, then run the same command with `--yes`. To list the allowed
`QA Scenario` values, see [Set any field with `--field`](commands.md#set-any-field-with---field).

## Replace an issue's description

1. Read the current issue so you start from its text:

   ```sh
   cubrid-jira search CBRD-XXXXX --no-recurse > CBRD-XXXXX.md
   ```

2. Edit the description in a new file, such as `notes.md`.
3. Preview the update and check the converted Jira wiki markup in the body:

   ```sh
   cubrid-jira update CBRD-XXXXX --description-file notes.md
   ```

4. Send it:

   ```sh
   cubrid-jira update CBRD-XXXXX --description-file notes.md --yes
   ```

5. Read the issue again. The update deleted the cached copy, so `search`
   fetches the new text:

   ```sh
   cubrid-jira search CBRD-XXXXX --no-recurse
   ```

## Edit your own comment

`comment-list` reports each author by display name, such as `Daehyun Kim`.

1. Find the ID of your latest comment:

   ```sh
   ID=$(cubrid-jira comment-list CBRD-XXXXX --output json \
     | jq -r '.comments[] | select(.author == "Your Name") | .id' | tail -1)
   ```

2. Write the new text in `edit.md` and preview the change:

   ```sh
   cubrid-jira comment-update CBRD-XXXXX --id "$ID" --body-file edit.md
   ```

3. Send it:

   ```sh
   cubrid-jira comment-update CBRD-XXXXX --id "$ID" --body-file edit.md --yes
   ```

`comment-delete` follows the same steps and cannot be undone.

## Resolve a bug as "Not a bug"

1. List the transitions and their allowed resolutions:

   ```sh
   cubrid-jira transition CBRD-XXXXX
   ```

2. Preview the transition with a resolution from that list:

   ```sh
   cubrid-jira transition CBRD-XXXXX --to "Bug Invalid" --resolution "Not a bug"
   ```

3. Send it:

   ```sh
   cubrid-jira transition CBRD-XXXXX --to "Bug Invalid" --resolution "Not a bug" --yes
   ```

If you leave out `--resolution`, the CLI refuses, because JIRA would otherwise
record `Fixed` and the resolution could not be changed later.

## Move a sub-task to another parent

1. Preview the six wizard requests:

   ```sh
   cubrid-jira reparent CBRD-XXXXX --to CBRD-YYYYY --output json
   ```

2. Send them:

   ```sh
   cubrid-jira reparent CBRD-XXXXX --to CBRD-YYYYY --yes
   ```

If the command prints `!!! ATOMICITY WARNING`, the issue is now a Task with no
parent. Run the `convert-to-subtask` command that the warning prints, once.

## Download attachments without pulling a core file

1. List the attachments and their sizes:

   ```sh
   cubrid-jira attachment CBRD-XXXXX --list
   ```

2. Download everything up to 50 MiB per file:

   ```sh
   cubrid-jira attachment CBRD-XXXXX --max-bytes 52428800 --out ./CBRD-XXXXX-files
   ```

Files over the limit show as `[SKIP]` in the output and are not downloaded.
