"""Regression coverage for the v2.12.1 lifecycle and replay corrections."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from core.chapter_change_preview import ChapterChangePreview
from core.derived_state_rebuilder import DerivedStateRebuilder
from core.foreshadow_manager import ForeshadowManager


@pytest.fixture
def manager(tmp_path):
    return ForeshadowManager(tmp_path, logging.getLogger(__name__))


@pytest.mark.parametrize('status', ['open', 'resolved', 'cancelled'])
def test_all_author_names_suppress_automatic_duplicates(manager, status):
    record = manager.create('Original')
    manager.update(record['id'], text='Intermediate')
    manager.update(record['id'], text='Current', status=status)
    entries = [{'text': name} for name in ('Original', 'Intermediate', 'Current')]
    assert manager.preview(3, entries) == []
    assert manager.ingest(3, entries)['introduced'] == 0
    manager.rebuild([(3, entries)])
    assert len(manager.list()) == 1
    assert manager.list()[0]['status'] == status


def test_rebuild_invalidates_old_automatic_editor_revision(manager):
    manager.ingest(1, [{'text': 'Seed', 'target_chapter': 5}])
    old = manager.list()[0]
    manager.rebuild([(1, [{'text': 'Seed', 'target_chapter': 20}])])
    new = manager.list()[0]
    assert old['id'] == new['id'] and new['revision'] > old['revision']
    with pytest.raises(ValueError, match='changed'):
        manager.update(old['id'], expected_revision=old['revision'], target_chapter=5)
    assert manager.list()[0]['target_chapter'] == 20


@pytest.mark.parametrize('remove', ['delete', 'empty_rebuild'])
def test_removed_then_reintroduced_identity_never_reuses_revision(manager, remove):
    manager.ingest(1, [{'text': 'Seed'}])
    old = manager.list()[0]
    if remove == 'delete':
        manager.delete(old['id'])
    else:
        manager.rebuild([])
    manager = ForeshadowManager(manager.path.parent, manager.logger)
    manager.ingest(1, [{'text': 'Seed'}])
    new = manager.list()[0]
    assert new['id'] == old['id'] and new['revision'] > old['revision']
    with pytest.raises(ValueError, match='changed'):
        manager.update(old['id'], expected_revision=old['revision'], notes='stale')


def test_legacy_revision_high_water_survives_removal(manager):
    manager.ingest(1, [{'text': 'Seed'}])
    data = manager._load()
    data.pop('revision_high_water')
    data['items'][0]['revision'] = 500
    manager.storage.atomic_write_json(manager.path, data)
    manager.rebuild([])
    manager.rebuild([(1, [{'text': 'Seed'}])])
    assert manager.list()[0]['revision'] > 500


def test_rebuild_reads_author_edits_at_commit_not_initial_snapshot(manager, monkeypatch):
    item = manager.create('Seed', notes='old')
    rebuilder = DerivedStateRebuilder(manager.path.parent, manager.logger)
    reached, proceed = Event(), Event()
    original_write = rebuilder.storage.atomic_write_json

    def pause_other_ledger(path, data, *args, **kwargs):
        if path.name == 'facts.json':
            reached.set()
            assert proceed.wait(10)
        return original_write(path, data, *args, **kwargs)

    monkeypatch.setattr(rebuilder.storage, 'atomic_write_json', pause_other_ledger)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rebuilder.rebuild)
        try:
            assert reached.wait(10)
            saved = manager.update(item['id'], expected_revision=item['revision'], notes='acknowledged')
        finally:
            proceed.set()
        future.result(timeout=15)
    final = manager.list()[0]
    assert final['notes'] == 'acknowledged' and final['revision'] == saved['revision']


def test_replay_commit_serializes_with_independent_author_writer(manager, monkeypatch):
    item = manager.create('Seed')
    reached, proceed, writer_started = Event(), Event(), Event()
    original_apply = manager._apply_entries

    def pause_replay(*args):
        reached.set()
        assert proceed.wait(10)
        return original_apply(*args)

    def edit():
        writer_started.set()
        return ForeshadowManager(manager.path.parent, manager.logger).update(
            item['id'], expected_revision=item['revision'], notes='saved after replay')

    monkeypatch.setattr(manager, '_apply_entries', pause_replay)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rebuild = pool.submit(manager.rebuild, [(1, [])])
        try:
            assert reached.wait(10)
            writer = pool.submit(edit)
            assert writer_started.wait(10)
            assert not writer.done()
        finally:
            proceed.set()
        rebuild.result(timeout=15)
        writer.result(timeout=15)
    assert manager.list()[0]['notes'] == 'saved after replay'


def test_failed_replay_leaves_live_ledger_unchanged(manager):
    manager.ingest(1, [{'text': 'Seed'}])
    before = manager.path.read_bytes()
    with pytest.raises(ValueError):
        manager.rebuild([(1, [{'text': 'Changed'}]), (2, None)])
    assert manager.path.read_bytes() == before


def test_clear_resolution_chapter_is_explicit_and_recorded(manager):
    item = manager.create('Seed', introduced_chapter=1)
    item = manager.update(item['id'], status='resolved', resolved_chapter=7)
    unchanged = manager.update(item['id'], notes='unrelated')
    assert unchanged['resolved_chapter'] == 7
    cleared = manager.update(item['id'], expected_revision=unchanged['revision'], resolved_chapter=None)
    assert 'resolved_chapter' not in cleared and cleared['status'] == 'resolved'
    assert cleared['history'][-1]['changes']['resolved_chapter'] == {'before': 7, 'after': None}
    with pytest.raises(ValueError):
        manager.update(item['id'], target_chapter=None)
    assert 'resolved_chapter' not in manager.list()[0]


@pytest.mark.parametrize('entries', [
    [{'text': 'Seed'}, {'text': 'Seed', 'action': 'resolve'}],
    [{'text': 'Seed'}, {'text': 'Seed'}, {'text': 'Seed', 'action': 'resolve'}, {'text': 'Seed', 'action': 'resolve'}],
    [{'text': 'Seed', 'action': 'resolve'}, {'text': 'Seed'}],
    [{'text': 'Seed', 'action': 'unknown'}, {'text': 'Seed', 'evidence_verified': False}],
])
def test_preview_and_commit_share_sequential_batch_transitions(manager, entries):
    preview = ChapterChangePreview(manager.path.parent, manager.logger)
    changes = preview._foreshadow_changes(1, {'foreshadowing': entries})
    assert not manager.path.exists()
    counts = manager.ingest(1, entries)
    assert counts['introduced'] == sum(c['action'] == 'create' for c in changes)
    assert counts['resolved'] == sum(c['action'] == 'change' and c['risk'] == 'low' for c in changes)
    assert counts['unmatched_resolutions'] == sum(c['risk'] == 'high' for c in changes)


def test_preview_id_resolution_and_future_introduction_match_commit(manager):
    manager.ingest(5, [{'text': 'Future'}])
    item = manager.list()[0]
    entries = [{'id': item['id'], 'action': 'resolve'}, {'text': 'Future'}]
    before = manager.path.read_bytes()
    changes = manager.preview(2, entries)
    assert len(changes) == 1 and not changes[0]['matched_id']
    assert manager.path.read_bytes() == before
    assert manager.ingest(2, entries) == {'introduced': 0, 'resolved': 0, 'unmatched_resolutions': 1}


def test_legacy_text_resolution_without_id_remains_supported(manager):
    manager.storage.atomic_write_json(manager.path, {'items': [
        {'text': 'Legacy', 'introduced_chapter': 1, 'status': 'open'}]})
    entries = [{'text': 'Legacy', 'action': 'resolve'}]
    changes = ChapterChangePreview(manager.path.parent)._foreshadow_changes(2, {'foreshadowing': entries})
    assert changes[0]['risk'] == 'low'
    assert manager.ingest(2, entries)['resolved'] == 1


def test_web_and_mcp_accept_explicit_resolution_clear(manager, monkeypatch):
    from ui import app as web
    import novel_server
    monkeypatch.setattr(web, 'get_novel_manager', lambda _: SimpleNamespace(path=manager.path.parent))
    monkeypatch.setattr(novel_server, 'fsh_', lambda: manager)

    async def run():
        item = manager.create('Seed')
        manager.update(item['id'], status='resolved', resolved_chapter=7)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://test') as client:
            response = await client.post('/api/novels/Demo/foreshadowing/' + item['id'], json={'resolved_chapter': None})
            assert response.status_code == 200
            assert 'resolved_chapter' not in response.json()['item']
        manager.update(item['id'], resolved_chapter=8)
        result = json.loads(await novel_server.update_foreshadow(item['id'], resolved_chapter=None))
        assert 'resolved_chapter' not in result
        tool = next(t for t in await novel_server.list_tools() if t.name == 'update_foreshadow')
        assert 'null' in tool.inputSchema['properties']['resolved_chapter']['type']

    asyncio.run(run())
