# Batch planning and portable reports

Available in v2.13 through Web Studio, MCP, and the command line. These operations do not call a model or rewrite chapter text.

## Web Studio

Open **Timeline → Foreshadow planner** (also available through Creative Assets).

- **Ownership** separates author-managed plans from summary-managed records. Quick views reset text/tag/status/priority/ownership filters to show all plans, overdue plans, upcoming payoffs, high-priority open records, or author plans. The planning clock and upcoming window stay unchanged.
- Select individual cards or **Select this page**, then **Batch edit selected**. Web selection is limited to the visible page (20 records), is not an implicit “select all matches,” and clears when the board refreshes or changes pages.
- Shift deadlines by a positive or negative chapter delta, or assign one absolute target. Change priorities or lifecycle status; add/remove exact tags using JSON arrays. Resolution chapter can be preserved, explicitly set, or cleared. Resolution metadata requires the resulting status to be `resolved`.
- **Preview changes** shows per-record before/after values, including transfer to author management. Nothing is saved by preview. Changing any input invalidates the preview. **Apply reviewed changes** commits the reviewed selection; one invalid or stale record rejects the whole batch.
- **Download all matches** exports the applied filters across pages, not just selected cards. JSON retains IDs and revisions for scripting; Markdown is a readable planning table. Web downloads require a complete result and reject matches exceeding 5000 rather than silently truncating.

All new controls support English (default), Chinese, and Japanese. Existing lifecycle rules and the latest 50 history entries per record are preserved. Batch history uses `author_batch`.

## Transaction semantics

Every selected record requires its current `expected_revision`. The same ledger lock used by single-record edits and rebuilds protects validation and the single atomic commit. Preview is not a reservation: another edit between preview and apply may invalidate the request. Refresh and review again after a conflict; do not remove revision checks or blindly retry a relative shift after an uncertain response.

Limits: 1–100 unique selections; chapter targets 1–1,000,001 and strictly after introduction; delta −1,000,000 to +1,000,000; at most 10 final tags, each at most 40 characters. Tags are exact, case-sensitive labels, and the same tag cannot appear in both add and remove lists. A legacy invalid target must be set to a valid absolute target before relative shifting. Invalid legacy tags require repair through the single-record editor before batch tag operations.

Every edited summary-managed record becomes author-managed and is preserved across replay. Explicitly reopening a record clears current resolution metadata. Duplicate open text remains invalid. A batch with no actual changes to existing author-managed records does not write or advance revisions. This is not a multi-file project transaction or an undo feature; normal backups remain available.

## MCP

1. Read `get_foreshadow_board(status="open", ownership="author")`, paginating if needed.
2. Build a request using the returned IDs/revisions, for example:

```json
{
  "selection": [
    {"id": "ID_FROM_BOARD", "expected_revision": 3}
  ],
  "changes": {
    "target_delta": 2,
    "priority": "high",
    "add_tags": ["Act II", "Arc, Part 1"]
  }
}
```

3. Pass this object to `batch_update_foreshadows`; `dry_run` defaults to `true`. Inspect `items[].changes`, then apply the same request with `dry_run=false` when the changes match the intended operation.
4. Use `export_foreshadow_report(status="open", max_items=1000)` for a portable JSON report.

The report is one filtered ledger snapshot, not repeated pagination reads. `summary` counts the entire project; `total_matches` counts the selected filters. `exported`, `max_items`, and `complete` make truncation explicit. The MCP/CLI default limit is 1000 and the maximum is 5000. Narrow filters if that is insufficient. History and internal aliases are excluded; `include_notes=true` adds notes, evidence, and resolution notes.

**Reports are not anonymous.** Text, tags, and other story metadata remain present even when notes are excluded. Review before sharing. Markdown escapes embedded HTML, Markdown links, and table delimiters. Reports are current planning views, not historical status snapshots or restorable project backups.

## Command line

```shell
novel-workspace foreshadows list --novel Demo --due overdue --ownership author
novel-workspace foreshadows list --novel Demo --offset 50 --limit 50
novel-workspace foreshadows batch --novel Demo --file batch.json
novel-workspace foreshadows batch --novel Demo --file batch.json --apply
novel-workspace foreshadows export --novel Demo --status open --format markdown --output plans.md
novel-workspace foreshadows export --novel Demo --format json --include-notes --max-items 5000
```

The batch file contains exactly `selection` and `changes`; file size is capped at 1 MiB. Applying requires the command-line `--apply` flag, not a flag hidden in the file. List and batch results are JSON. Export supports JSON or Markdown. Exit codes: `0` success/complete, `1` invalid request or failure, `2` explicitly partial export. A partial CLI report still contains the bounded results and `complete=false`.

Output files must be new files outside runtime storage; publication is atomic and does not overwrite an existing destination. The novel must be registered and its resolved directory must remain within the configured novels root. None of these commands switches the current novel.

## HTTP endpoints

- `GET /api/novels/{name}/foreshadow-board` adds `ownership=all|author|summary`.
- `POST /api/novels/{name}/foreshadow-batch` takes `selection`, `changes`, and optional `dry_run`.
- `GET /api/novels/{name}/foreshadow-report` takes board filters (without offset/limit), `max_items`, `include_notes`, and `format=json|markdown`.

Existing registered-project and access-control rules apply. Successful reports use `Cache-Control: no-store` and download disposition. A stale or invalid batch, unsupported format, or incomplete Web report returns an error rather than applying partial changes or downloading a misleading file.
