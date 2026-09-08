"""Bounded, read-only manuscript diagnostics shared by Web, CLI and MCP.

Units are Han/Kana characters plus Unicode letter/digit word runs. Paragraphs
are nonempty physical lines; repetition is whitespace-normalized and exact.
These are descriptive heuristics, not quality scores or a transactional snapshot.
"""
from collections import defaultdict
import hashlib
import math
from pathlib import Path
import re
import statistics

MAX_FILES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_PARAGRAPHS = 100000
MAX_GROUPS = 100
MAX_OCCURRENCES = 20
UNIT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\u3040-\u30ff]|[^\W_\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\u3040-\u30ff]+", re.UNICODE)


def _integer(name, value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")


def inspect_manuscript(novel_path, *, start_chapter=1, end_chapter=None,
                       target_units=0, units_per_minute=300,
                       min_repeat_chars=40, include_excerpts=False):
    """Inspect direct chapter text files without changing project state or text."""
    _integer("start_chapter", start_chapter, 1, 1000000)
    if end_chapter is not None:
        _integer("end_chapter", end_chapter, start_chapter, 1000000)
    _integer("target_units", target_units, 0, 1000000)
    _integer("units_per_minute", units_per_minute, 1, 10000)
    _integer("min_repeat_chars", min_repeat_chars, 10, 1000)
    if type(include_excerpts) is not bool:
        raise ValueError("include_excerpts must be a boolean")
    root = Path(novel_path).resolve(strict=True)
    directory = root / "chapters"
    issues, chapters = [], []
    report = {
        "schema_version": 1, "complete": True,
        "settings": dict(start_chapter=start_chapter, end_chapter=end_chapter,
                         target_units=target_units, units_per_minute=units_per_minute,
                         min_repeat_chars=min_repeat_chars, include_excerpts=include_excerpts),
        "chapters": chapters, "issues": issues, "duplicates": [],
        "limits": {"files": MAX_FILES, "file_bytes": MAX_FILE_BYTES,
                   "total_bytes": MAX_TOTAL_BYTES, "paragraphs": MAX_PARAGRAPHS,
                   "duplicate_groups": MAX_GROUPS, "occurrences_per_group": MAX_OCCURRENCES},
    }

    def issue(code, message, **details):
        issues.append(dict(code=code, message=message, **details))

    if directory.is_symlink() or (directory.exists() and directory.resolve() != directory):
        raise ValueError("Chapter directory must not be a symbolic link or junction")
    if not directory.is_dir():
        issue("missing_directory", "No chapter directory exists.")
        entries = []
    else:
        entries = []
        for path in directory.iterdir():
            entries.append(path)
            if len(entries) > MAX_FILES:
                raise ValueError(f"Chapter directory exceeds the {MAX_FILES}-entry scan limit")
    identities = defaultdict(list)
    for path in sorted(entries, key=lambda p: p.name):
        if path.suffix.lower() != ".txt":
            continue
        if not re.fullmatch(r"[0-9]{1,7}", path.stem) or not 1 <= int(path.stem) <= 1000000:
            issue("invalid_filename", "Text file has no supported positive chapter number.", file=path.name)
            report["complete"] = False
            continue
        number = int(path.stem)
        if number < start_chapter or (end_chapter is not None and number > end_chapter):
            continue
        identities[number].append(path)
        if path.name != f"{number:06d}.txt":
            issue("noncanonical_filename", "Chapter filename differs from the canonical six-digit .txt format; the chapter editor may not find it.",
                  chapter=number, file=path.name)

    last = end_chapter if end_chapter is not None else max(identities, default=start_chapter - 1)
    cursor = start_chapter
    missing = []
    for number in sorted(identities):
        if number > cursor:
            missing.append({"start": cursor, "end": number - 1})
        cursor = number + 1
    if cursor <= last:
        missing.append({"start": cursor, "end": last})
    for gap in missing:
        issue("missing_chapters", "Chapter numbers are absent from the selected range.", **gap)

    repeated = {}
    total_bytes = paragraph_count = 0
    for number, paths in sorted(identities.items()):
        if len(paths) != 1:
            issue("ambiguous_chapter", "Multiple filenames identify the same chapter; all are skipped.",
                  chapter=number, files=[p.name for p in paths])
            report["complete"] = False
            continue
        path = paths[0]
        try:
            if path.is_symlink() or path.resolve() != path or not path.is_file():
                raise ValueError("Linked or non-regular chapter file is skipped")
            before = path.stat()
            if before.st_size > MAX_FILE_BYTES:
                raise ValueError("Chapter exceeds the per-file byte limit")
            if total_bytes + before.st_size > MAX_TOTAL_BYTES:
                raise ValueError("Chapter exceeds the remaining total byte budget")
            with path.open("rb") as stream:
                raw = stream.read(min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total_bytes) + 1)
            total_bytes += len(raw)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError("Chapter changed during the scan; retry when editing has stopped")
            if len(raw) != before.st_size:
                raise ValueError("Chapter size changed during the scan")
            content = raw.decode("utf-8-sig")
        except (OSError, ValueError) as exc:
            issue("skipped_chapter", str(exc), chapter=number, file=path.name)
            report["complete"] = False
            continue
        lines = content.splitlines()
        units = sum(token[0].isalnum() for token in UNIT_RE.findall(content))
        chapter = {"chapter": number, "file": path.name, "units": units,
                   "characters": sum(not c.isspace() for c in content),
                   "paragraphs": sum(bool(line.strip()) for line in lines),
                   "reading_minutes": math.ceil(units / units_per_minute),
                   "sha256": hashlib.sha256(raw).hexdigest()}
        if target_units:
            chapter["target_percent"] = round(units / target_units * 100, 1)
        chapters.append(chapter)
        if not content.strip():
            issue("empty_chapter", "Chapter contains no non-whitespace text.", chapter=number)
        for line_number, line in enumerate(lines, 1):
            normalized = " ".join(line.split())
            if len(normalized) < min_repeat_chars:
                continue
            paragraph_count += 1
            if paragraph_count > MAX_PARAGRAPHS:
                raise ValueError("Manuscript exceeds the repeat-analysis paragraph limit")
            fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            item = repeated.setdefault(fingerprint, {"fingerprint": fingerprint, "characters": len(normalized),
                                                     "occurrence_count": 0, "chapters": set(), "locations": []})
            item["occurrence_count"] += 1
            item["chapters"].add(number)
            if len(item["locations"]) < MAX_OCCURRENCES:
                item["locations"].append({"chapter": number, "line": line_number})
            if include_excerpts and "excerpt" not in item:
                item["excerpt"] = normalized[:200]

    median = statistics.median(c["units"] for c in chapters if c["units"]) if any(c["units"] for c in chapters) else 0
    previous = None
    for chapter in chapters:
        chapter["delta_units"] = None if previous is None else chapter["units"] - previous
        previous = chapter["units"]
        if len(chapters) >= 3 and median and chapter["units"] and (chapter["units"] < median * .5 or chapter["units"] > median * 2):
            issue("length_outlier", "Length is below half or above twice the nonzero chapter median (heuristic).",
                  chapter=chapter["chapter"])
    duplicates = [item for item in repeated.values() if len(item["chapters"]) > 1]
    duplicates.sort(key=lambda item: (-item["occurrence_count"], item["fingerprint"]))
    for item in duplicates[:MAX_GROUPS]:
        item["chapter_count"] = len(item.pop("chapters"))
        item["locations_truncated"] = item["occurrence_count"] > len(item["locations"])
        report["duplicates"].append(item)
    total_units = sum(c["units"] for c in chapters)
    report["summary"] = {
        "chapters_found": len(identities), "chapters_scanned": len(chapters),
        "total_units": total_units, "total_characters": sum(c["characters"] for c in chapters),
        "reading_minutes": math.ceil(total_units / units_per_minute),
        "median_units": median, "missing_count": sum(g["end"] - g["start"] + 1 for g in missing),
        "empty_count": sum(i["code"] == "empty_chapter" for i in issues),
        "issue_count": len(issues), "duplicate_groups": len(duplicates),
        "duplicates_truncated": len(duplicates) > MAX_GROUPS,
    }
    return report


def render_markdown(report):
    """Portable English report; excerpts are included only when requested."""
    def escape(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    summary = report["summary"]
    lines = ["# Manuscript diagnostics", "", f"Complete scan: {report['complete']}",
             "", "Read-only heuristics, not a quality score or a transactional snapshot.",
             "Units: Han/Kana characters plus Unicode letter/digit word runs. Paragraphs: nonempty physical lines.",
             "Excerpts may contain private manuscript text; review before sharing.", "", "## Summary", ""]
    lines += [f"- {key}: {value}" for key, value in summary.items()]
    lines += ["", "## Settings", ""]
    lines += [f"- {key}: {value}" for key, value in report["settings"].items()]
    lines += ["", "## Chapters", "", "Chapter | File | Units | Delta | Reading minutes | SHA256", "--- | --- | ---: | ---: | ---: | ---"]
    for item in report["chapters"]:
        lines.append(" | ".join(escape(item[key]) for key in ("chapter", "file", "units", "delta_units", "reading_minutes", "sha256")))
    lines += ["", "## Findings", ""]
    for item in report["issues"]:
        lines.append("- " + escape(item))
    lines += ["", "## Cross-chapter repeated paragraphs", ""]
    for item in report["duplicates"]:
        lines += [f"- {item['fingerprint']}: {item['occurrence_count']} occurrences in {item['chapter_count']} chapters."]
        lines += [f"  - Chapter {loc['chapter']}, line {loc['line']}" for loc in item["locations"]]
        if item["locations_truncated"]:
            lines.append("  - Additional locations omitted by report limits.")
        if "excerpt" in item:
            lines.append("  - Excerpt: " + escape(item["excerpt"]))
    lines += ["", "## Limits", ""]
    lines += [f"- {key}: {value}" for key, value in report["limits"].items()]
    return "\n".join(lines) + "\n"
