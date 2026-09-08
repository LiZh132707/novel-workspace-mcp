"""Read-only relationship projection shared by Web Studio and MCP."""
import re

from core.entity_ledger import EntityLedger


ROLE_TIERS = ("主角", "重要配角", "次要角色", "NPC", "路人")


def character_network(characters, chapter=None, character=None, role_tier=None):
    EntityLedger._validate_chapter(chapter)
    if role_tier is not None and role_tier not in ROLE_TIERS:
        raise ValueError("Unknown character role tier")
    roster = characters.list_characters()
    registered = {item["name"] for item in roster}
    active = {item["name"]: item for item in characters.list_characters(chapter)}
    nodes = {
        name: {
            "id": name, "level": item["ability_level"], "role_tier": item["role_tier"],
            "status": item["status"], "registered": True,
        }
        for name, item in active.items()
    }
    snapshot = EntityLedger(characters.path.parent, characters.logger).relationship_snapshot(chapter)
    edges = {}
    for item in snapshot["relationships"]:
        # Do not resurrect a registered NPC outside its configured appearance range.
        if any(name in registered and name not in active for name in (item["from"], item["to"])):
            continue
        for name in (item["from"], item["to"]):
            nodes.setdefault(name, {"id": name, "registered": False, "role_tier": "unregistered", "level": "", "status": ""})
        edges[(item["from"], item["to"])] = {**item, "source": "ledger"}

    notes = []
    if chapter is None:
        # Undated prose is never projected backwards into a historical query.
        for name in active:
            detail = characters.get_character(name) or {}
            text = detail.get("relationships", "")
            if not isinstance(text, str):
                continue
            for fragment in re.split(r"[,，;；\n]+", text):
                fragment = fragment.strip()
                if not fragment:
                    continue
                target, kind = None, "Relationship"
                if fragment in registered:
                    target = fragment
                else:
                    parts = re.split(r"[:：]", fragment, maxsplit=1)
                    if len(parts) == 2:
                        left, right = (part.strip() for part in parts)
                        if left in registered and right not in registered:
                            target, kind = left, right or kind
                        elif right in registered and left not in registered:
                            target, kind = right, left or kind
                if target and target != name:
                    edges.setdefault((name, target), {
                        "from": name, "to": target, "type": kind, "strength": None,
                        "chapter": None, "evidence": fragment, "source": "profile",
                    })
                else:
                    notes.append({"character": name, "text": fragment})

    if character is not None and character not in nodes:
        raise ValueError("Character is not present in this relationship view")
    seeds = set(nodes)
    if character is not None:
        seeds &= {character}
    if role_tier is not None:
        seeds &= {name for name, item in nodes.items() if item["role_tier"] == role_tier}
    filtered = character is not None or role_tier is not None
    selected_edges = [item for item in edges.values() if not filtered or item["from"] in seeds or item["to"] in seeds]
    included = seeds | {name for item in selected_edges for name in (item["from"], item["to"])}
    pairs = {(item["from"], item["to"]) for item in selected_edges if item["source"] == "ledger"}
    return {
        "nodes": [nodes[name] for name in sorted(included)],
        "edges": sorted(selected_edges, key=lambda item: (item["from"], item["to"])),
        "history": [item for item in snapshot["history"] if (item["from"], item["to"]) in pairs],
        "profile_notes": [note for note in notes if note["character"] in seeds],
        "filters": {"chapter": chapter, "character": character, "role_tier": role_tier},
    }
