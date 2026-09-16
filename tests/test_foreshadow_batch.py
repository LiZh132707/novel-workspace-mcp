import logging
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.foreshadow_manager import ForeshadowManager
from core.foreshadow_report import render_foreshadow_report


@pytest.fixture
def manager(tmp_path):
    return ForeshadowManager(tmp_path, logging.getLogger(__name__))


def selection(*items):
    return [{'id': item['id'], 'expected_revision': item['revision']} for item in items]


def test_batch_previews_by_default_and_commits_once(manager, monkeypatch):
    a = manager.create('A', target_chapter=8, tags=['Old', 'Keep'])
    b = manager.create('B', target_chapter=12)
    before = manager.path.read_bytes()
    changes = {'target_delta': 3, 'priority': 'high', 'add_tags': ['Arc, Part 1'], 'remove_tags': ['Old']}
    preview = manager.batch_update(selection(a, b), changes)
    assert preview['dry_run'] and not preview['applied'] and preview['changed'] == 2
    assert manager.path.read_bytes() == before
    writes = []
    original = manager.storage.atomic_write_json
    def write(path, data):
        writes.append(path)
        original(path, data)
    monkeypatch.setattr(manager.storage, 'atomic_write_json', write)
    result = manager.batch_update(selection(a, b), changes, dry_run=False)
    assert result['items'] == preview['items'] and result['applied']
    assert writes == [manager.path]
    items = manager.list()
    assert [item['target_chapter'] for item in items] == [11, 15]
    assert items[0]['tags'] == ['Keep', 'Arc, Part 1']
    assert items[1]['tags'] == ['Arc, Part 1']
    assert all(i['priority'] == 'high' and i['history'][-1]['action'] == 'author_batch' for i in items)


def test_batch_marks_automatic_records_author_managed(manager):
    manager.ingest(1, [{'text': 'Seed'}])
    item = manager.list()[0]
    preview = manager.batch_update(selection(item), {'priority': 'high'})
    assert preview['items'][0]['changes']['author_managed']['after'] is True
    manager.batch_update(selection(item), {'priority': 'high'}, False)
    manager.rebuild([(1, [{'text': 'Seed'}]), (2, [{'text': 'Seed', 'action': 'resolve'}])])
    assert manager.list()[0]['status'] == 'open'


def test_late_stale_revision_aborts_whole_batch(manager):
    a, b = manager.create('A'), manager.create('B')
    manager.update(b['id'], notes='another editor')
    before = manager.path.read_bytes()
    with pytest.raises(ValueError, match='changed'):
        manager.batch_update(selection(a, b), {'priority': 'high'}, False)
    assert manager.path.read_bytes() == before


def test_preview_does_not_reserve_stale_commit(manager):
    item = manager.create('A')
    manager.batch_update(selection(item), {'target_delta': 5})
    manager.update(item['id'], target_chapter=20)
    with pytest.raises(ValueError, match='changed'):
        manager.batch_update(selection(item), {'target_delta': 5}, False)
    assert manager.list()[0]['target_chapter'] == 20


@pytest.mark.parametrize('changes', [
    {}, [], {'unknown': 1}, {'notes': 'not a batch field'}, {'target_delta': True},
    {'target_delta': 1.5}, {'target_delta': 1000001}, {'target_delta': -1000001},
    {'target_chapter': 5, 'target_delta': 2}, {'target_chapter': 0}, {'target_chapter': 1000002},
    {'priority': 'urgent'}, {'status': 'bad'}, {'add_tags': 'bad'}, {'add_tags': [None]},
    {'add_tags': ['x'] * 11}, {'remove_tags': ['x'], 'add_tags': ['x']}, {'resolved_chapter': 2},
    {'resolution_note': 'x'}, {'resolved_chapter': None}, {'status': 'resolved', 'resolved_chapter': True},
])
def test_invalid_batch_changes_never_write(manager, changes):
    item = manager.create('A')
    before = manager.path.read_bytes()
    with pytest.raises(ValueError):
        manager.batch_update(selection(item), changes, False)
    assert manager.path.read_bytes() == before


@pytest.mark.parametrize('selected', [[], {}, [None], [{}], [{'id': 'x'}],
    [{'id': 'x', 'expected_revision': True}], [{'id': '', 'expected_revision': 0}],
    [{'id': 'x', 'expected_revision': 0, 'extra': 1}], [{'id': 'x', 'expected_revision': 0}] * 101])
def test_invalid_selection_rejected(manager, selected):
    with pytest.raises(ValueError):
        manager.batch_update(selected, {'priority': 'high'})
    assert not manager.path.exists()


def test_duplicate_ids_missing_ids_and_string_dry_run_rejected(manager):
    item = manager.create('A')
    before = manager.path.read_bytes()
    for selected in (selection(item, item), selection(item) + [{'id': 'missing', 'expected_revision': 0}]):
        with pytest.raises(ValueError):
            manager.batch_update(selected, {'priority': 'high'}, False)
    with pytest.raises(ValueError, match='boolean'):
        manager.batch_update(selection(item), {'priority': 'high'}, 'false')
    assert manager.path.read_bytes() == before


def test_last_target_failure_leaves_first_unchanged(manager):
    a = manager.create('A', target_chapter=20)
    b = manager.create('B', introduced_chapter=7, target_chapter=8)
    before = manager.path.read_bytes()
    with pytest.raises(ValueError):
        manager.batch_update(selection(a, b), {'target_delta': -2}, False)
    assert manager.path.read_bytes() == before


def test_tag_limit_after_merge_rejects_batch(manager):
    a = manager.create('A')
    b = manager.create('B', tags=[str(i) for i in range(10)])
    before = manager.path.read_bytes()
    with pytest.raises(ValueError):
        manager.batch_update(selection(a, b), {'add_tags': ['Extra']}, False)
    assert manager.path.read_bytes() == before


def test_resolve_clear_reopen_cancel_and_duplicate_reopen(manager):
    a, b = manager.create('A'), manager.create('B')
    manager.batch_update(selection(a, b), {'status': 'resolved', 'resolved_chapter': 4, 'resolution_note': 'Done'}, False)
    manager.batch_update(selection(*manager.list()), {'resolved_chapter': None}, False)
    assert all('resolved_chapter' not in i and i['resolution_note'] == 'Done' for i in manager.list())
    manager.batch_update(selection(*manager.list()), {'status': 'open'}, False)
    assert all('resolution_note' not in i and 'resolved_at' not in i for i in manager.list())
    manager.batch_update(selection(*manager.list()), {'status': 'cancelled'}, False)
    manager.create('B')
    before = manager.path.read_bytes()
    with pytest.raises(ValueError, match='exact text'):
        manager.batch_update(selection(*manager.list()[:2]), {'status': 'open'}, False)
    assert manager.path.read_bytes() == before


def test_noop_does_not_write_or_increment_revision(manager):
    item = manager.create('A', priority='high')
    before = manager.path.read_bytes()
    result = manager.batch_update(selection(item), {'priority': 'high'}, False)
    assert not result['applied'] and result['changed'] == 0
    assert manager.path.read_bytes() == before


def test_two_independent_batch_writers_do_not_lose_updates(manager):
    items = [manager.create('A'), manager.create('B')]
    def edit(delta):
        try:
            ForeshadowManager(manager.path.parent, manager.logger).batch_update(selection(*items), {'target_delta': delta}, False)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, [2, 5])) == [False, True]
    assert manager.list()[0]['target_chapter'] == manager.list()[1]['target_chapter']


def test_failed_publish_keeps_ledger_unchanged(manager, monkeypatch):
    item = manager.create('A')
    before = manager.path.read_bytes()
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr(manager.storage, 'atomic_write_json', fail)
    with pytest.raises(OSError, match='disk full'):
        manager.batch_update(selection(item), {'priority': 'high'}, False)
    assert manager.path.read_bytes() == before


def test_highest_introduction_uses_valid_default_target(manager):
    assert manager.create('Last', introduced_chapter=1000000)['target_chapter'] == 1000001


def test_report_single_snapshot_filters_ownership_and_privacy(manager, monkeypatch):
    manager.create('Manual', target_chapter=4, notes='private', tags=['Plot'])
    manager.ingest(1, [{'text': 'Auto', 'evidence': 'private evidence'}])
    reads = []
    original = manager._load
    def load():
        reads.append(True)
        return original()
    monkeypatch.setattr(manager, '_load', load)
    report = manager.report(5, ownership='author')
    assert len(reads) == 1
    assert report['complete'] and report['total_matches'] == report['exported'] == 1
    assert report['summary']['total'] == 2
    assert report['items'][0]['text'] == 'Manual' and 'notes' not in report['items'][0]
    assert 'history' not in report['items'][0]
    assert manager.report(5, ownership='summary', include_notes=True)['items'][0]['evidence'] == 'private evidence'
    assert manager.board(5, ownership='author')['items'][0]['overdue']


def test_report_full_filtered_results_not_one_page(manager):
    for i in range(25):
        manager.create(f'Seed {i}')
    assert len(manager.board(limit=20)['items']) == 20
    assert len(manager.report()['items']) == 25
    partial = manager.report(max_items=20)
    assert not partial['complete'] and partial['total_matches'] == 25 and partial['exported'] == 20


@pytest.mark.parametrize('options', [{'max_items': 0}, {'max_items': 5001}, {'max_items': True},
    {'include_notes': 'false'}, {'ownership': 'invalid'}, {'current_chapter': True}])
def test_report_validation(manager, options):
    with pytest.raises(ValueError):
        manager.report(**options)


def test_legacy_manual_ownership_and_report_escaping(manager):
    manager.storage.atomic_write_json(manager.path, {'items': [
        {'id': 'old', 'source': 'manual', 'status': 'open', 'text': '<script> | [link](https://x)\nTitle', 'notes': 'secret'}]})
    report = manager.report(ownership='author')
    body = render_foreshadow_report(report)
    assert '&lt;script&gt;' in body and '\\|' in body and '\\[link\\]' in body
    assert '<script>' not in body and 'secret' not in body
    assert report['items'][0]['author_managed'] is True
    assert 'secret' in render_foreshadow_report(manager.report(include_notes=True))
