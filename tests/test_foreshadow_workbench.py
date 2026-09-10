import logging
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import httpx

from core.foreshadow_manager import ForeshadowManager


@pytest.fixture
def manager(tmp_path):
    return ForeshadowManager(tmp_path, logging.getLogger('foreshadow-tests'))


def test_resolution_requires_exact_unambiguous_identity(manager):
    manager.ingest(1, [{'text': 'Red key'}, {'text': 'Red key origin'}])
    result = manager.ingest(3, [{'action': 'resolve', 'text': 'key'}])
    assert result['resolved'] == 0
    assert all(i['status'] == 'open' for i in manager.list())


def test_resolution_never_precedes_introduction(manager):
    manager.ingest(10, [{'text': 'Red key'}])
    assert manager.ingest(3, [{'action': 'resolve', 'text': 'Red key'}])['resolved'] == 0


def test_reopening_clears_resolution_metadata(manager):
    manager.ingest(1, [{'text': 'Red key'}])
    manager.ingest(3, [{'action': 'resolve', 'text': 'Red key'}])
    item = manager.list()[0]
    reopened = manager.update(item['id'], status='open')
    assert 'resolved_chapter' not in reopened
    assert 'resolved_at' not in reopened


def test_invalid_status_does_not_silently_succeed(manager):
    manager.ingest(1, [{'text': 'Red key'}])
    before = manager.path.read_bytes()
    with pytest.raises(ValueError):
        manager.update(manager.list()[0]['id'], status='typo')
    assert manager.path.read_bytes() == before


def test_manual_create_reschedule_resolve_reopen_and_cancel(manager):
    item = manager.create('The missing seal', introduced_chapter=2, target_chapter=8,
                          priority='high', tags=['Mystery', 'Mystery'], notes='Author plan')
    assert item['author_managed'] and item['revision'] == 1 and item['tags'] == ['Mystery']
    updated = manager.update(item['id'], expected_revision=1, target_chapter=12)
    assert updated['revision'] == 2 and updated['target_chapter'] == 12
    resolved = manager.update(item['id'], expected_revision=2, status='resolved', resolved_chapter=10, resolution_note='Seal found')
    assert resolved['resolved_chapter'] == 10 and resolved['resolved_at']
    reopened = manager.update(item['id'], expected_revision=3, status='open')
    assert all(k not in reopened for k in ('resolved_at', 'resolved_chapter', 'resolution_note'))
    assert manager.update(item['id'], status='cancelled')['status'] == 'cancelled'
    assert len(manager.list()[0]['history']) == 5


@pytest.mark.parametrize('fields', [
    {'text': ''}, {'text': None}, {'priority': 'urgent'}, {'tags': 'not-list'}, {'tags': [None]},
    {'tags': ['x']*11}, {'notes': 'x'*4001}, {'target_chapter': True}, {'target_chapter': 1.5},
    {'target_chapter': -1}, {'target_chapter': 1}, {'status': {}}, {'id': 'new-id'},
    {'resolved_chapter': 4}, {'resolution_note': 'not resolved'}, {'unknown': 'x'},
])
def test_invalid_edits_are_atomic(manager, fields):
    item = manager.create('Seed', introduced_chapter=1)
    before = manager.path.read_bytes()
    with pytest.raises(ValueError):
        manager.update(item['id'], **fields)
    assert manager.path.read_bytes() == before


def test_stale_revision_is_rejected_and_history_bounded(manager):
    item = manager.create('Seed')
    manager.update(item['id'], expected_revision=item['revision'], notes='new')
    before = manager.path.read_bytes()
    with pytest.raises(ValueError, match='changed'):
        manager.update(item['id'], expected_revision=item['revision'], notes='stale')
    assert manager.path.read_bytes() == before
    for index in range(55):
        manager.update(item['id'], notes=str(index))
    assert len(manager.list()[0]['history']) == 50
    assert manager.list()[0]['revision'] == 57


def test_duplicate_open_text_is_rejected_on_create_edit_and_reopen(manager):
    first = manager.create('Seed')
    with pytest.raises(ValueError):
        manager.create('Seed')
    second = manager.create('Other')
    with pytest.raises(ValueError):
        manager.update(second['id'], text='Seed')
    manager.update(first['id'], status='cancelled')
    manager.create('Seed')
    with pytest.raises(ValueError):
        manager.update(first['id'], status='open')


def test_unknown_action_and_unverified_resolution_do_not_change_state(manager):
    manager.ingest(1, [{'text': 'Seed'}])
    assert manager.ingest(2, [{'text': 'New', 'action': 'typo'}])['introduced'] == 0
    assert manager.ingest(3, [{'text': 'Seed', 'action': 'resolve', 'evidence_verified': False}])['resolved'] == 0
    assert manager.list()[0]['status'] == 'open'


def test_exact_id_resolution_and_idempotent_replay(manager):
    manager.ingest(1, [{'text': 'Seed'}])
    item = manager.list()[0]
    assert manager.ingest(3, [{'id': item['id'], 'action': 'resolve', 'evidence': 'Found'}])['resolved'] == 1
    assert manager.list()[0]['resolution_note'] == 'Found'
    assert manager.ingest(1, [{'text': 'Seed'}])['introduced'] == 0
    assert len(manager.list()) == 1


def test_ambiguous_text_or_id_does_not_auto_resolve(manager):
    manager.ingest(1, [{'text': 'Seed'}])
    data = manager._load()
    data['items'].append(dict(data['items'][0]))
    manager.storage.atomic_write_json(manager.path, data)
    assert manager.ingest(2, [{'text': 'Seed', 'action': 'resolve'}])['resolved'] == 0
    with pytest.raises(ValueError, match='Ambiguous'):
        manager.update(data['items'][0]['id'], notes='x')


def test_author_control_survives_summary_replay(manager):
    manual = manager.create('Manual', target_chapter=7)
    manager.ingest(1, [{'text': 'Automatic'}])
    auto = next(i for i in manager.list() if i['text'] == 'Automatic')
    managed = manager.update(auto['id'], text='Renamed', status='cancelled', notes='Keep this')
    summaries = manager.path.parent / 'summaries'
    summaries.mkdir()
    manager.storage.atomic_write_json(summaries / '000001.json', {
        'chapter': 1, 'foreshadowing': [{'text': 'Automatic'}, {'text': 'Manual'}]})
    manager.storage.atomic_write_json(summaries / '000002.json', {
        'chapter': 2, 'foreshadowing': [{'text': 'Manual', 'action': 'resolve'}]})
    from core.derived_state_rebuilder import DerivedStateRebuilder
    DerivedStateRebuilder(manager.path.parent, manager.logger).rebuild(2)
    items = manager.list()
    assert len(items) == 2
    assert next(i for i in items if i['id'] == manual['id'])['status'] == 'open'
    assert next(i for i in items if i['id'] == managed['id'])['notes'] == 'Keep this'
    assert next(i for i in items if i['id'] == managed['id'])['text'] == 'Renamed'


def test_stable_summary_ids_survive_rebuild(manager):
    manager.ingest(1, [{'text': 'Automatic'}])
    item_id = manager.list()[0]['id']
    summaries = manager.path.parent / 'summaries'
    summaries.mkdir()
    manager.storage.atomic_write_json(summaries / '000001.json', {'chapter': 1, 'foreshadowing': [{'text': 'Automatic'}]})
    manager.storage.atomic_write_json(summaries / '000002.json', {'chapter': 2, 'foreshadowing': [{'action': 'resolve', 'id': item_id}]})
    from core.derived_state_rebuilder import DerivedStateRebuilder
    DerivedStateRebuilder(manager.path.parent, manager.logger).rebuild(2)
    assert manager.list()[0]['id'] == item_id
    assert manager.list()[0]['status'] == 'resolved'


def test_board_boundaries_filters_counts_and_pagination(manager):
    for text, target in [('Overdue', 4), ('Today', 5), ('Soon', 10), ('Later', 11)]:
        manager.create(text, target_chapter=target, tags=['Plot'], priority='high' if target == 4 else 'normal')
    cancelled = manager.create('Cancelled', target_chapter=3)
    manager.update(cancelled['id'], status='cancelled')
    board = manager.board(current_chapter=5, limit=2)
    assert board['summary'] == dict(total=5, open=4, resolved=0, cancelled=1, overdue=1, due_soon=2, invalid=0)
    assert [i['text'] for i in board['items']] == ['Overdue', 'Today']
    assert board['has_more'] and board['tags'] == ['Plot']
    assert manager.board(5, offset=2, limit=2)['items'][0]['text'] == 'Soon'
    result = manager.board(5, query='over', tag='plot', status='open', due='overdue', priority='high')
    assert result['total_matches'] == 1 and result['items'][0]['remaining_chapters'] == -1
    assert result['summary']['total'] == 5
    assert manager.board(5, due_within=0, due='due_soon')['items'][0]['text'] == 'Today'
    assert manager.board(5, offset=100)['items'] == []


@pytest.mark.parametrize('options', [{'limit': 0}, {'limit': 101}, {'offset': -1}, {'current_chapter': True},
    {'status': 'bad'}, {'due': 'bad'}, {'priority': 'bad'}, {'query': 'x'*201}, {'tag': 'x'*41}, {'due_within': -1}])
def test_invalid_board_filters(manager, options):
    with pytest.raises(ValueError):
        manager.board(**options)


def test_legacy_fields_do_not_crash_board_and_extras_survive_edits(manager):
    manager.storage.atomic_write_json(manager.path, {'custom': 5, 'items': [
        {'id': 'old', 'text': 'Seed', 'status': 'open', 'target_chapter': 'broken', 'introduced_chapter': '1', 'tags': None},
        {'id': 'bad', 'status': ['invalid']} ]})
    board = manager.board(5)
    assert board['summary']['invalid'] == 1
    assert next(i for i in board['items'] if i['id'] == 'old')['due_state'] == 'unplanned'
    manager.update('old', target_chapter=10)
    assert manager._load()['custom'] == 5


def test_concurrent_writers_do_not_lose_items(manager):
    def create(index):
        return ForeshadowManager(manager.path.parent, manager.logger).create(f'Seed {index}')
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(create, range(12)))
    assert len(manager.list()) == 12 and len({i['id'] for i in results}) == 12


def test_only_one_concurrent_revision_update_succeeds(manager):
    item = manager.create('Seed')
    def edit(note):
        try:
            ForeshadowManager(manager.path.parent, manager.logger).update(item['id'], expected_revision=1, notes=note)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ['a', 'b'])) == [False, True]


def test_preview_uses_same_exact_matching_policy(manager):
    from core.chapter_change_preview import ChapterChangePreview
    manager.ingest(1, [{'text': 'Red key origin'}])
    preview = ChapterChangePreview(manager.path.parent, manager.logger)
    changes = preview._foreshadow_changes(3, {'foreshadowing': [{'text': 'key', 'action': 'resolve'}]})
    assert changes[0]['risk'] == 'high' and not changes[0]['matched_id']


def test_web_mcp_create_edit_board_and_project_isolation(manager, monkeypatch):
    from ui import app as web
    import novel_server
    from fastapi import HTTPException
    def lookup(name):
        if name != 'Demo':
            raise HTTPException(404, 'Unknown project')
        return SimpleNamespace(path=manager.path.parent, get_current_chapter=lambda: 5)
    monkeypatch.setattr(web, 'get_novel_manager', lookup)
    monkeypatch.setattr(novel_server, 'nm', lambda: lookup('Demo'))
    monkeypatch.setattr(novel_server, 'fsh_', lambda: manager)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://test') as client:
            prefix='/api/novels/Demo'
            created=await client.post(prefix+'/foreshadowing', json={'text':'Web seed','target_chapter':7})
            assert created.status_code == 200
            item=created.json()['item']
            response=await client.get(prefix+'/foreshadow-board?due=due_soon')
            assert response.headers['cache-control'] == 'no-store'
            assert response.json()['board'] == json.loads(await novel_server.get_foreshadow_board(due='due_soon'))
            saved=await client.post(prefix+'/foreshadowing/'+item['id'], json={'status':'resolved','resolved_chapter':5,'expected_revision':1})
            assert saved.json()['item']['status'] == 'resolved'
            assert (await client.post(prefix+'/foreshadowing/'+item['id'],json={'notes':'stale','expected_revision':1})).status_code == 400
            assert (await client.post(prefix+'/foreshadowing',json=[])).status_code == 400
            assert (await client.post(prefix+'/foreshadowing/'+item['id'],json=[])).status_code == 400
            assert (await client.get(prefix+'/foreshadow-board?limit=0')).status_code == 400
            for suffix in ('/foreshadowing','/foreshadow-board'):
                assert (await client.get('/api/novels/Unknown'+suffix)).status_code == 404
            mcp=json.loads(await novel_server.create_foreshadow('MCP seed', target_chapter=12))
            updated=json.loads(await novel_server.update_foreshadow(mcp['id'], expected_revision=mcp['revision'], status='cancelled'))
            assert updated['status'] == 'cancelled'
        catalog={t.name for t in await novel_server.list_tools()}
        for name in ('get_foreshadow_board','create_foreshadow','update_foreshadow'):
            assert name in catalog and name in novel_server.HANDLERS and name not in novel_server.GLOBAL_TOOLS
    asyncio.run(run())
