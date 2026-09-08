# Manuscript diagnostics

The v2.11 workbench is a read-only, model-free inspection of direct text files in a project's `chapters` directory. It does not inspect drafts, savepoints, summaries, or backup copies. It never updates canonical story state, word counts, or manuscript text.

## Entry points

- **Web:** Dashboard → Manuscript diagnostics. Run with the default settings or choose an inclusive chapter range, length target, reading speed, repeat threshold, and optional private excerpts. The chart and chapter table share pages of 50 chapters. Findings show the first 100 entries; exports include all findings. Downloads perform a fresh scan using the settings of the displayed report, so edits made in the meantime may change the result.
- **CLI:** a registered project name is required. Markdown goes to stdout by default; `--json` selects JSON. `--output` creates a new UTF-8 file outside runtime storage and never overwrites an existing file.
- **MCP:** open a project, then call `inspect_manuscript`. All settings are optional and match the engine's defaults. The tool returns JSON and runs off the event loop. Model access is not required.

```sh
novel-workspace inspect --novel MyNovel
novel-workspace inspect --novel MyNovel --start-chapter 3 --end-chapter 20 --target-units 3000 --json
novel-workspace inspect --novel MyNovel --units-per-minute 250 --min-repeat-chars 60 --output manuscript-report.md
```

CLI exit codes: **0** means the selected files were inspected, **2** means some files were skipped or invalid, and **1** means the command failed (invalid settings, hard resource limit, I/O error, or output conflict). Exit 0 does **not** mean there are no missing chapters, empty chapters, repeated paragraphs, or other findings. Inspect `summary.issue_count` and `summary.duplicate_groups` for those.

The Web API is `GET /api/novels/{name}/manuscript-report`, with `format=view` (wrapped JSON), `format=json`, or `format=markdown`. It uses the same registered-project lookup and access controls as other project routes. Responses use `Cache-Control: no-store`.

## Settings

| Setting | Default | Accepted values |
| --- | ---: | --- |
| `start_chapter` | 1 | Integer, 1–1,000,000 |
| `end_chapter` | Unset | Inclusive end, at least the start and at most 1,000,000 |
| `target_units` | 0 | Integer, 0–1,000,000; 0 hides target percentages |
| `units_per_minute` | 300 | Integer, 1–10,000 |
| `min_repeat_chars` | 40 | Integer, 10–1,000 normalized characters |
| `include_excerpts` | false | Boolean; CLI uses `--include-excerpts` |

## Metric definitions

- **Units:** individual Han/Kana characters plus runs of other Unicode letters/digits. Spaces, punctuation, and underscores are separators. This multilingual heuristic is not a tokenizer, linguistic segmentation engine, or the application's stored word count.
- **Characters:** non-whitespace Unicode characters, including punctuation.
- **Reading time:** units divided by the configured speed, rounded up to whole minutes. Totals are calculated independently; rounded chapter times need not add up to the rounded total. This is an estimate, not a reading-speed assessment.
- **Length delta:** difference from the previous successfully scanned chapter in the selected range. It can cross missing or skipped chapters.
- **Outliers:** nonzero length below half or above twice the nonzero median, when at least three chapters were scanned. Short intentional scenes may be flagged; no automatic rewriting or quality score is applied.
- **Repeated paragraphs:** each nonempty physical line is treated as a paragraph. Whitespace runs are collapsed, then matching is exact and case/punctuation sensitive. A group must occur in at least two different chapters. Each location is a 1-based chapter/line reference. It does not detect paraphrases or compare against outside works. A single-chapter repeat is not a cross-chapter finding.
- **SHA-256:** each chapter's source-byte hash supports comparing reports, not proof of ownership or authenticity. The whole scan is not a transactional snapshot. Files detected changing during a read are skipped; stop editing and retry for stable comparisons.

Missing numbers are reported as compact ranges, up to the last observed chapter if no end is provided. `state.json`'s current chapter is not used to infer an intended ending. Duplicate numeric identities (for example, `1.txt` and `000001.txt`) are skipped rather than arbitrarily choosing one. Noncanonical numeric filenames are inspected and flagged because the chapter editor expects a six-digit `.txt` name. Nonnumeric/unsupported `.txt` names make the scan incomplete even if a chapter filter is active, because they cannot be assigned to a range. Non-text files and nested directories without `.txt` names are ignored.

## Resource limits and privacy

The scan accepts at most 5,000 direct directory entries, 2 MiB per chapter, 20 MiB total input, and 100,000 substantial paragraph candidates. Oversized, unreadable, malformed UTF-8, ambiguous, or linked chapter files are skipped with `complete=false`. Directory links/junctions and hard entry/paragraph limits fail explicitly. UTF-8 BOM is supported.

Reports include at most 100 duplicate groups and 20 sampled locations per group. Totals and truncation flags remain visible. Samples may not include every affected chapter. Filename, hash, size, and line metadata are still present with excerpts off; reports are not anonymous. Excerpts opt in to up to 200 normalized characters per group. Review reports before sharing, especially when using a remote MCP client. Files are never uploaded by this engine.

Structured findings and export prose are English. The new Web controls support English, Chinese, and Japanese. Existing analysis tools also now count short sentences in pacing metrics and preserve the true locations of repeated paragraph openings.
