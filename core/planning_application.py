"""分阶段策划应用时的正史保护规则。"""
from __future__ import annotations


def protect_committed_opening(old_opening: dict, new_opening: dict, current_chapter: int) -> dict:
    old_opening = old_opening if isinstance(old_opening, dict) else {}
    new_opening = new_opening if isinstance(new_opening, dict) else {}
    current_chapter = max(0, int(current_chapter))
    past = {}
    for item in old_opening.get("chapters", []) if isinstance(old_opening.get("chapters"), list) else []:
        if isinstance(item, dict) and 0 < int(item.get("chapter", 0)) <= current_chapter:
            past[int(item["chapter"])] = item
    future = {}
    for item in new_opening.get("chapters", []) if isinstance(new_opening.get("chapters"), list) else []:
        if isinstance(item, dict) and int(item.get("chapter", 0)) > current_chapter:
            future[int(item["chapter"])] = item
    return {
        **new_opening,
        "chapters": [value for _, value in sorted({**past, **future}.items())],
        "protected_chapters": sorted(past),
    }
