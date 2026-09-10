"""伏笔生命周期管理。"""
from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from filelock import FileLock

from storage_utils import StorageManager


class ForeshadowManager:
    def __init__(self, novel_path: Path, logger, storage: StorageManager | None = None):
        self.path = novel_path / "foreshadowing.json"
        self.logger = logger
        self.storage = storage or StorageManager(logger)

    def ingest(self, chapter: int, entries: list) -> dict:
        self._integer('chapter', chapter, 1, 1000000)
        if not isinstance(entries, list):
            raise ValueError('entries must be a list')
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._ingest(chapter, entries)

    def _ingest(self, chapter: int, entries: list) -> dict:
        data = self._load()
        result, _ = self._apply_entries(data, chapter, entries)
        self._save(data)
        return result

    @staticmethod
    def _author_managed(item):
        return bool(item.get('author_managed') or item.get('source') == 'manual')

    def _remember_revisions(self, data):
        data['revision_high_water'] = max(
            [self._chapter_number(data.get('revision_high_water'))]
            + [self._chapter_number(item.get('revision')) for item in data['items']])

    def _save(self, data):
        self._remember_revisions(data)
        self.storage.atomic_write_json(self.path, data)

    def preview(self, chapter, entries):
        """Run the same ordered transitions as ingestion without publishing writes."""
        self._integer('chapter', chapter, 1, 1000000)
        if not isinstance(entries, list):
            raise ValueError('entries must be a list')
        _, changes = self._apply_entries(deepcopy(self._load()), chapter, entries)
        return changes

    def rebuild(self, chapters):
        """Replay in memory and atomically publish under the author-edit lock.

        Read author records only after taking the lock. Persist a high-water mark
        even for deleted records so deterministic IDs never reuse old revisions.
        """
        with FileLock(str(self.path) + '.transaction.lock', timeout=30):
            data = self._load()
            self._remember_revisions(data)
            data['items'] = [item for item in data['items'] if self._author_managed(item)]
            for chapter, entries in chapters:
                self._integer('chapter', chapter, 1, 1000000)
                if not isinstance(entries, list):
                    raise ValueError('entries must be a list')
                self._apply_entries(data, chapter, entries)
            self._save(data)

    def _apply_entries(self, data, chapter, entries):
        """Apply lifecycle actions sequentially to an isolated in-memory ledger."""
        introduced = resolved = unmatched = 0
        changes = []
        self._remember_revisions(data)
        for raw in entries:
            item = raw if isinstance(raw, dict) else {"action": "introduce", "text": str(raw)}
            if item.get("evidence_verified") is False:
                continue
            text = str(item.get("text", "")).strip()
            if not text and not item.get('id'):
                continue
            action = item.get("action", "introduce")
            if action == "resolve":
                match = self.match_resolution(data['items'], item, chapter)
                if match and self._author_managed(match):
                    match = None
                changes.append(dict(action='resolve', text=text, matched=match is not None,
                                    matched_id=match.get('id', '') if match else '',
                                    before=match.get('text', '') if match else ''))
                if match:
                    before = dict(match)
                    match.update({"status": "resolved", "resolved_chapter": chapter, "resolved_at": datetime.now().isoformat(),
                                  'resolution_note': str(item.get('evidence', ''))[:2000]})
                    self._record(match, before, 'summary_resolve')
                    resolved += 1
                else:
                    unmatched += 1
                continue
            if action != 'introduce' or not text:
                continue
            if any(self._author_managed(value) and text in self._aliases(value) for value in data['items']):
                continue
            if any(value.get("status") == "open" and value.get("text") == text for value in data["items"]):
                continue
            stable_id = uuid.uuid5(uuid.NAMESPACE_URL, f'foreshadow:{chapter}:{text}').hex
            if any(value.get('id') == stable_id for value in data['items']):
                continue
            try:
                target = int(item.get("target_chapter") or chapter + 10)
            except (TypeError, ValueError, OverflowError):
                target = chapter + 10
            self._remember_revisions(data)
            data['revision_high_water'] += 1
            record = {
                "id": stable_id, "text": text, "introduced_chapter": chapter,
                "target_chapter": min(1000001, max(chapter + 1, target)), "status": "open",
                "evidence": str(item.get("evidence", ""))[:500],
                "created_at": datetime.now().isoformat(), 'revision': data['revision_high_water'], 'source': 'chapter_summary',
            }
            data['items'].append(record)
            changes.append(dict(action='introduce', text=text, target_chapter=record['target_chapter']))
            introduced += 1
        self._remember_revisions(data)
        return {"introduced": introduced, "resolved": resolved, 'unmatched_resolutions': unmatched}, changes

    @staticmethod
    def _aliases(item):
        aliases = item.get('text_aliases', [])
        aliases = aliases if isinstance(aliases, list) else []
        return {value for value in [item.get('text'), item.get('origin_text'), *aliases] if isinstance(value, str)}

    @classmethod
    def match_resolution(cls, items, entry, chapter):
        """Resolve only one exact ID/text match whose introduction is not in the future."""
        candidates = [value for value in items if value.get('status') == 'open'
                      and cls._chapter_number(value.get('introduced_chapter')) <= chapter
                      and (value.get('id') == entry['id'] if entry.get('id') else
                           bool(str(entry.get('text', '')).strip()) and
                           str(value.get('text', '')).strip() == str(entry.get('text', '')).strip())]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _integer(name, value, minimum=0, maximum=1000001):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
        return value

    @staticmethod
    def _text(name, value, maximum, allow_empty=True):
        if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
            raise ValueError(f'{name} must be text of at most {maximum} characters' + ('' if allow_empty else ' and must not be empty'))
        return value.strip()

    def _fields(self, values):
        allowed = {'text', 'target_chapter', 'status', 'priority', 'tags', 'notes', 'resolved_chapter', 'resolution_note'}
        if set(values) - allowed:
            raise ValueError('Unknown foreshadow fields: ' + ', '.join(sorted(set(values) - allowed)))
        result = dict(values)
        for key, maximum in (('text', 2000), ('notes', 4000), ('resolution_note', 2000)):
            if key in result:
                result[key] = self._text(key, result[key], maximum, key != 'text')
        for key in ('target_chapter', 'resolved_chapter'):
            if key in result:
                if key == 'resolved_chapter' and result[key] is None:
                    continue
                self._integer(key, result[key], 1)
        if 'status' in result and result['status'] not in ('open', 'resolved', 'cancelled'):
            raise ValueError('status must be open, resolved or cancelled')
        if 'priority' in result and result['priority'] not in ('low', 'normal', 'high'):
            raise ValueError('priority must be low, normal or high')
        if 'tags' in result:
            tags = result['tags']
            if not isinstance(tags, list) or len(tags) > 10:
                raise ValueError('tags must be a list of at most 10 labels')
            result['tags'] = list(dict.fromkeys(self._text('tag', tag, 40, False) for tag in tags))
        return result

    @staticmethod
    def _record(item, before, action):
        fields = ('text', 'target_chapter', 'status', 'priority', 'tags', 'notes', 'resolved_chapter', 'resolution_note', 'author_managed')
        changes = {key: {'before': before.get(key), 'after': item.get(key)} for key in fields if before.get(key) != item.get(key)}
        if not changes:
            return
        now = datetime.now().isoformat()
        revision = ForeshadowManager._chapter_number(item.get('revision')) + 1
        history = item.get('history', [])
        history = history if isinstance(history, list) else []
        item.update(updated_at=now, revision=revision,
                    history=(history + [{'at': now, 'action': action, 'revision': revision, 'changes': changes}])[-50:])

    def create(self, text, introduced_chapter=0, target_chapter=None, priority='normal', tags=None, notes=''):
        """Create an author-managed plan; chapter zero means not yet introduced."""
        self._integer('introduced_chapter', introduced_chapter, 0, 1000000)
        fields = self._fields(dict(text=text, target_chapter=target_chapter if target_chapter is not None else introduced_chapter + 10,
                                   priority=priority, tags=tags if tags is not None else [], notes=notes))
        if fields['target_chapter'] <= introduced_chapter:
            raise ValueError('target_chapter must follow introduced_chapter')
        with FileLock(str(self.path) + '.transaction.lock', timeout=30):
            data = self._load()
            if any(i.get('status') == 'open' and i.get('text') == fields['text'] for i in data['items']):
                raise ValueError('An open foreshadow with this exact text already exists')
            item = dict(fields, id=uuid.uuid4().hex, introduced_chapter=introduced_chapter, status='open',
                        source='manual', author_managed=True, origin_text=fields['text'], revision=0,
                        created_at=datetime.now().isoformat())
            self._record(item, {}, 'create')
            data['items'].append(item)
            self._save(data)
            return item

    def list(self, current_chapter: int | None = None) -> list[dict]:
        items = self._load()["items"]
        for item in items:
            try:
                target_chapter = int(item.get("target_chapter", 0))
            except (TypeError, ValueError, OverflowError):
                target_chapter = 0
            item["overdue"] = bool(
                current_chapter is not None and item.get("status") == "open"
                and target_chapter > 0 and current_chapter > target_chapter
            )
        return items

    def open_items(self, current_chapter: int, limit: int = 20) -> list[dict]:
        items = [item for item in self.list(current_chapter) if item.get("status") == "open"]
        return sorted(items, key=lambda item: (
            not item.get("overdue", False), self._chapter_number(item.get("target_chapter")),
        ))[:limit]

    def update(self, item_id: str, expected_revision=None, **values) -> dict:
        self._text('item_id', item_id, 200, False)
        if expected_revision is not None:
            self._integer('expected_revision', expected_revision, 0, 1000000000)
        values = self._fields(values)
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._update(item_id, expected_revision=expected_revision, **values)

    def _update(self, item_id: str, expected_revision=None, **values) -> dict:
        data = self._load()
        item = next((entry for entry in data["items"] if entry.get("id") == item_id), None)
        if not item:
            raise ValueError('Foreshadow not found')
        if sum(entry.get('id') == item_id for entry in data['items']) != 1:
            raise ValueError('Ambiguous foreshadow ID; repair the ledger before editing')
        if expected_revision is not None and expected_revision != self._chapter_number(item.get('revision')):
            raise ValueError('Foreshadow changed since it was loaded; refresh before saving')
        before = dict(item)
        introduced = self._chapter_number(item.get('introduced_chapter'))
        if 'target_chapter' in values and values['target_chapter'] <= introduced:
            raise ValueError('target_chapter must follow introduced_chapter')
        status = values.get('status', item.get('status'))
        if any(k in values for k in ('resolved_chapter', 'resolution_note')) and status != 'resolved':
            raise ValueError('Resolution metadata requires resolved status')
        if values.get('resolved_chapter') is not None and values['resolved_chapter'] < introduced:
            raise ValueError('resolved_chapter must not precede introduction')
        if status == 'open' and any(other is not item and other.get('status') == 'open' and
                                  other.get('text') == values.get('text', item.get('text')) for other in data['items']):
            raise ValueError('Another open foreshadow has this exact text')
        item.update(values)
        if item.get('resolved_chapter') is None:
            item.pop('resolved_chapter', None)
        if status != 'resolved':
            for key in ('resolved_chapter', 'resolved_at', 'resolution_note'):
                item.pop(key, None)
        elif before.get('status') != 'resolved':
            item['resolved_at'] = datetime.now().isoformat()
        item['author_managed'] = True
        item.setdefault('origin_text', before.get('text', ''))
        item['text_aliases'] = sorted(self._aliases(before) | self._aliases(item))
        self._record(item, before, 'author_update')
        self._save(data)
        return item

    def delete(self, item_id: str) -> bool:
        with FileLock(str(self.path) + ".transaction.lock", timeout=30):
            return self._delete(item_id)

    def _delete(self, item_id: str) -> bool:
        data = self._load()
        self._remember_revisions(data)
        before = len(data["items"])
        data["items"] = [item for item in data["items"] if item.get("id") != item_id]
        self._save(data)
        return len(data["items"]) < before

    def _load(self) -> dict:
        data = self.storage.safe_read_json(self.path, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        return {
            **(data if isinstance(data, dict) else {}),
            "items": [dict(item) for item in items if isinstance(item, dict)]
            if isinstance(items, list) else [],
        }

    @staticmethod
    def _chapter_number(value) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    def board(self, current_chapter=0, status='all', due='all', query='', tag='', priority='all',
              due_within=5, offset=0, limit=50):
        self._integer('current_chapter', current_chapter, 0, 1000000)
        self._integer('due_within', due_within, 0, 1000000)
        self._integer('offset', offset, 0, 1000000)
        self._integer('limit', limit, 1, 100)
        if status not in ('all', 'open', 'resolved', 'cancelled'):
            raise ValueError('Invalid status filter')
        if due not in ('all', 'overdue', 'due_soon', 'scheduled', 'unplanned', 'closed'):
            raise ValueError('Invalid due filter')
        if priority not in ('all', 'low', 'normal', 'high'):
            raise ValueError('Invalid priority filter')
        query = self._text('query', query, 200).casefold()
        tag = self._text('tag', tag, 40).casefold()
        items = self.list(current_chapter)
        summary = dict(total=len(items), open=0, resolved=0, cancelled=0, overdue=0, due_soon=0, invalid=0)
        all_tags = set()
        for item in items:
            state = item.get('status')
            if state in ('open', 'resolved', 'cancelled'):
                summary[state] += 1
            else:
                summary['invalid'] += 1
            target = self._chapter_number(item.get('target_chapter'))
            item['due_state'] = ('closed' if state != 'open' else 'unplanned' if not target else
                                 'overdue' if target < current_chapter else
                                 'due_soon' if target <= current_chapter + due_within else 'scheduled')
            if item['due_state'] in ('overdue', 'due_soon'):
                summary[item['due_state']] += 1
            item['remaining_chapters'] = target - current_chapter if state == 'open' and target else None
            item['priority'] = item.get('priority') if item.get('priority') in ('low', 'normal', 'high') else 'normal'
            item['tags'] = [t for t in item.get('tags', []) if isinstance(t, str)] if isinstance(item.get('tags', []), list) else []
            item['revision'] = self._chapter_number(item.get('revision'))
            all_tags.update(item['tags'])
        filtered = [i for i in items if (status == 'all' or i.get('status') == status)
                    and (due == 'all' or i['due_state'] == due)
                    and (priority == 'all' or i['priority'] == priority)
                    and (not tag or tag in [t.casefold() for t in i['tags']])
                    and (not query or query in ' '.join(str(i.get(k, '')) for k in ('text', 'notes', 'evidence', 'tags')).casefold())]
        order = {'overdue': 0, 'due_soon': 1, 'scheduled': 2, 'unplanned': 3, 'closed': 4}
        filtered.sort(key=lambda i: (order[i['due_state']], {'high': 0, 'normal': 1, 'low': 2}[i['priority']],
                                    self._chapter_number(i.get('target_chapter')), str(i.get('id', ''))))
        return dict(summary=summary, items=filtered[offset:offset + limit], total_matches=len(filtered),
                    offset=offset, limit=limit, has_more=offset + limit < len(filtered), tags=sorted(all_tags),
                    current_chapter=current_chapter, due_within=due_within)
