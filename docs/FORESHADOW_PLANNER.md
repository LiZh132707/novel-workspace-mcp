# Foreshadow planner

The v2.12 planner brings payoff planning and lifecycle editing to **Timeline → Foreshadow planner**. The former Creative Assets foreshadow controls now open the same planner. Existing project files remain compatible; no migration command is required. These actions edit the foreshadow ledger, not chapter text, and make no model calls.

## Lifecycle and ownership

- **Plant:** provide nonempty text, an introduction chapter (0 means a plan not yet introduced), and a target chapter strictly after introduction. Optional priority, tags, and author notes organize the plan.
- **Edit / reschedule:** change text, notes, tags, priority, or the target. Introduction chapter is fixed after creation. Rejected input does not partially save other fields.
- **Resolve:** select `resolved`, optionally record the actual chapter and a resolution note. The actual chapter must not precede introduction. If it is unknown, clear the optional field to remove a previously recorded chapter. API/MCP callers send `resolved_chapter: null` to clear it; omitting the field preserves the current value. The recorded timestamp is edit time, not story time.
- **Cancel:** select `cancelled` to retain the record without a pending payoff. The planner does not expose permanent deletion.
- **Reopen:** select `open`. Old resolution chapter, timestamp, and note are removed from current fields and retained in recent change history. Duplicate open text is rejected on creation, renaming, and reopening.

**Manual creation or any manual update transfers lifecycle control to the author.** The `author_managed` flag means automatic summary ingestion will neither duplicate nor resolve that plan. This includes edits to notes and targets, and updates through older APIs or the production issue center. Author-managed records survive `DerivedStateRebuilder` replay with their IDs, states, notes, and history. They remain author plans even if the underlying chapters have changed; authors must review whether they still apply. As of v2.12.1, foreshadow replay stages changes in memory and publishes under the same lock as author edits, reading the latest author records after acquiring that lock. A failed foreshadow replay leaves its live ledger unchanged. Other derived ledgers do not share a global multi-file transaction; avoid overlapping independent full rebuilds. There is no switch back to automatic lifecycle control. Current, original, and subsequent recorded names suppress automatic reintroduction even for resolved or cancelled plans.

Unedited summary-managed records continue automatic tracking. Resolution requires a unique exact ID match, or a unique exact trimmed text match when no ID is supplied. Matching is case-sensitive; substring guesses are no longer accepted. No automatic resolution may precede introduction, and explicitly unverified evidence is ignored. Unmatched, ambiguous, and author-managed resolution attempts are counted as `unmatched_resolutions`. New summary-derived IDs are deterministic by introduction chapter and exact text, supporting ID-based replay. Legacy IDs are not rewritten until a derived-state rebuild. Preview and ingestion use the same sequential lifecycle reducer, including introduction followed by resolution in one batch. Preview uses the same matching rules and marks unmatched resolution requests as high-risk rather than pretending they applied.

## Board semantics

The board is a **current planning view, not historical state**. `current_chapter` only supplies a planning clock; it does not reconstruct past statuses. If omitted, the Web/MCP adapters use the current project's chapter.

| Due state | Meaning for an open record |
| --- | --- |
| `overdue` | Target is strictly less than the planning clock |
| `due_soon` | Target is between the clock and clock + `due_within`, inclusive |
| `scheduled` | Target is later than that window |
| `unplanned` | A legacy record has no parseable positive target |
| `closed` | The record is not open |

Default upcoming window: **5 chapters**. Sorting is due category, high/normal/low priority, target chapter, then ID. Summary cards count the whole project, while `total_matches` counts the filtered result. Text search is case-insensitive across text, notes, evidence, and tags. Tag filtering is case-insensitive exact matching. Status/priority/due filters intersect.

Web pages contain 20 records. API/MCP pages default to 50 and accept `limit=1..100`, plus `offset`. No full-project truncation is silently implied by a page: check `total_matches` and `has_more`. The **Copy visible page as JSON** button copies only the loaded page, including private text, notes, evidence, and history. Review it before sharing; it is not an anonymous export or a full-project backup. Browsers without clipboard support report an error without changing records.

## MCP examples

```text
get_foreshadow_board(current_chapter=12, due="overdue", priority="high")
get_foreshadow_board(tag="Mystery", query="key", offset=0, limit=20)
create_foreshadow(text="The missing station key", introduced_chapter=2,
                 target_chapter=10, priority="high", tags=["Mystery"])
update_foreshadow(item_id="ID_FROM_BOARD", expected_revision=1,
                 status="resolved", resolved_chapter=9,
                 resolution_note="The key is found in the station office.")
update_foreshadow(item_id="ID_FROM_BOARD", expected_revision=2,
                 status="open", target_chapter=18)
```

The Web endpoints are `GET /api/novels/{name}/foreshadow-board`, `POST /api/novels/{name}/foreshadowing`, and the existing `POST /api/novels/{name}/foreshadowing/{item_id}`. They use registered-project lookup and existing application access controls. Existing `list_foreshadowing` retains its list-shaped result.

Edits through the new UI always send `expected_revision`. A stale edit is rejected before saving; close the editor, refresh, then review the newer state. The revision is optional for backward-compatible callers, which retain last-write-wins behavior. Automatic records receive newer revisions after replay; a persisted ledger high-water mark also prevents revision reuse when a deterministic ID disappears and is later reintroduced. IDs stay stable, but revision values are opaque concurrency tokens, not change counts. File locks serialize ledger writers, and recent changes include before/after values. History is bounded to the latest **50** events and is not a tamper-proof audit log or an automatic undo system. Reopening and rescheduling provide reversible lifecycle changes; project backups remain the way to restore an earlier file state.

## Validation limits

- Introduction: integer 0–1,000,000; target/resolution: integer 1–1,000,001.
- Text: 1–2,000 characters after trimming; notes: at most 4,000; resolution note: at most 2,000.
- Tags: at most 10 nonempty strings of at most 40 characters; exact duplicates are removed. The Web editor uses a JSON array such as `["Mystery", "Arc, Part 1"]`, preserving commas, quotes, and embedded newlines. An empty input clears all tags; invalid JSON is rejected without saving.
- Search: at most 200 characters; tag filter: at most 40; offset/window: 0–1,000,000.
- Only `open`, `resolved`, `cancelled` statuses and `high`, `normal`, `low` priorities are accepted. Booleans and fractional chapter values are rejected rather than coerced.

Malformed legacy metadata is displayed conservatively where possible. Duplicate IDs are rejected for editing; they require ledger repair. Existing storage backup/recovery behavior is unchanged. No zero-loss guarantee is made for a corrupt ledger or external tools that rewrite these files directly.
