import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from core import manuscript_diagnostics as diagnostics


@pytest.fixture
def novel(tmp_path):
    root = tmp_path / "novels" / "Demo"
    (root / "chapters").mkdir(parents=True)
    (root / "state.json").write_text('{"total_words": 999}', encoding="utf-8")
    return root


def chapter(novel, number, content):
    path = novel / "chapters" / f"{number:06d}.txt"
    path.write_text(content, encoding="utf-8")
    return path


def test_multilingual_metrics_and_exact_source_hash_are_read_only(novel):
    path = chapter(novel, 1, "Hello world 汉字 カナ café 123\n\n")
    before = {p: p.read_bytes() for p in novel.rglob('*') if p.is_file()}
    result = diagnostics.inspect_manuscript(novel, target_units=10, units_per_minute=2)
    item = result['chapters'][0]
    assert item['units'] == 8
    assert item['target_percent'] == 80
    assert item['reading_minutes'] == 4
    assert item['paragraphs'] == 1
    assert item['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result['complete'] and result['summary']['total_units'] == 8
    assert before == {p: p.read_bytes() for p in novel.rglob('*') if p.is_file()}


def test_missing_ranges_are_compact_and_empty_chapters_are_visible(novel):
    chapter(novel, 1, 'one')
    chapter(novel, 1000000, '\n\t')
    report = diagnostics.inspect_manuscript(novel)
    assert report['summary']['missing_count'] == 999998
    assert report['summary']['empty_count'] == 1
    gaps = [i for i in report['issues'] if i['code'] == 'missing_chapters']
    assert len(gaps) == 1 and gaps[0]['start'] == 2 and gaps[0]['end'] == 999999


def test_range_filters_include_end_and_ignore_outside_text(novel):
    for number in (1, 3, 4, 5):
        chapter(novel, number, 'word')
    report = diagnostics.inspect_manuscript(novel, start_chapter=2, end_chapter=4)
    assert [c['chapter'] for c in report['chapters']] == [3, 4]
    assert report['summary']['missing_count'] == 1


def test_explicit_empty_range_is_reported(novel):
    report = diagnostics.inspect_manuscript(novel, start_chapter=8, end_chapter=10)
    assert report['summary']['missing_count'] == 3
    assert report['summary']['chapters_scanned'] == 0


def test_noncanonical_chapter_is_inspected_but_flagged(novel):
    (novel / 'chapters' / '1.TXT').write_text('hello', encoding='utf-8')
    result = diagnostics.inspect_manuscript(novel)
    assert result['chapters'][0]['units'] == 1
    assert result['issues'][0]['code'] == 'noncanonical_filename'


def test_kana_block_punctuation_is_not_a_reading_unit_and_bom_is_ignored(novel):
    chapter(novel, 1, '\ufeffカ・ナ。！？😀_')
    report = diagnostics.inspect_manuscript(novel)
    assert report['chapters'][0]['units'] == 2


def test_missing_directory_does_not_get_created(tmp_path):
    report = diagnostics.inspect_manuscript(tmp_path)
    assert report['issues'][0]['code'] == 'missing_directory'
    assert not (tmp_path / 'chapters').exists()


def test_duplicate_identity_and_invalid_names_are_never_silently_counted(novel):
    chapter(novel, 1, 'canonical')
    for name in ('1.txt', 'notes.txt', '000000.txt', '１００.txt'):
        (novel / 'chapters' / name).write_text('ignored', encoding='utf-8')
    report = diagnostics.inspect_manuscript(novel)
    assert not report['complete']
    assert report['chapters'] == []
    assert sum(i['code'] == 'invalid_filename' for i in report['issues']) == 3
    assert any(i['code'] == 'ambiguous_chapter' for i in report['issues'])


def test_repeat_locations_normalization_and_privacy(novel):
    paragraph = 'A very long paragraph describing the quiet street.'
    chapter(novel, 1, '\n' + paragraph + '\n' + paragraph)
    chapter(novel, 2, '\n\n' + paragraph.replace(' ', '  '))
    report = diagnostics.inspect_manuscript(novel)
    group = report['duplicates'][0]
    assert group['occurrence_count'] == 3 and group['chapter_count'] == 2
    assert group['locations'] == [{'chapter': 1, 'line': 2}, {'chapter': 1, 'line': 3}, {'chapter': 2, 'line': 3}]
    assert 'excerpt' not in group
    assert paragraph not in json.dumps(report)
    assert diagnostics.inspect_manuscript(novel, include_excerpts=True)['duplicates'][0]['excerpt'] == paragraph


def test_same_chapter_repetition_and_case_changes_are_not_cross_chapter_matches(novel):
    paragraph = 'A sufficiently long sentence about a silent forest.'
    chapter(novel, 1, paragraph + '\n' + paragraph)
    chapter(novel, 2, paragraph.upper())
    assert diagnostics.inspect_manuscript(novel)['duplicates'] == []


def test_repeat_group_and_location_limits_are_explicit(novel, monkeypatch):
    monkeypatch.setattr(diagnostics, 'MAX_GROUPS', 1)
    monkeypatch.setattr(diagnostics, 'MAX_OCCURRENCES', 1)
    for number in (1, 2):
        chapter(novel, number, ('a' * 50 + '\n' + 'b' * 50 + '\n') * 2)
    report = diagnostics.inspect_manuscript(novel)
    assert report['summary']['duplicate_groups'] == 2
    assert report['summary']['duplicates_truncated']
    assert len(report['duplicates']) == 1
    assert report['duplicates'][0]['locations_truncated']
    assert len(report['duplicates'][0]['locations']) == 1


def test_length_outlier_and_trend(novel):
    for number, count in enumerate((10, 10, 30), 1):
        chapter(novel, number, 'word ' * count)
    report = diagnostics.inspect_manuscript(novel)
    assert [c['delta_units'] for c in report['chapters']] == [None, 0, 20]
    assert [i['chapter'] for i in report['issues'] if i['code'] == 'length_outlier'] == [3]


@pytest.mark.parametrize('options', [
    {'start_chapter': 0}, {'start_chapter': True}, {'start_chapter': 1.5},
    {'end_chapter': 1000001}, {'start_chapter': 3, 'end_chapter': 2},
    {'target_units': -1}, {'target_units': True}, {'units_per_minute': 0},
    {'min_repeat_chars': 9}, {'min_repeat_chars': 1001}, {'include_excerpts': 'false'},
])
def test_invalid_options_fail_before_scanning(novel, options):
    with pytest.raises(ValueError):
        diagnostics.inspect_manuscript(novel, **options)


def test_invalid_utf8_and_large_files_are_partial(novel, monkeypatch):
    chapter(novel, 1, 'a' * 50)
    chapter(novel, 2, '').write_bytes(b'\xff\xfe')
    monkeypatch.setattr(diagnostics, 'MAX_FILE_BYTES', 10)
    report = diagnostics.inspect_manuscript(novel)
    assert not report['complete'] and not report['chapters']
    assert len(report['issues']) == 2


def test_total_budget_marks_skipped_chapters(novel, monkeypatch):
    chapter(novel, 1, 'first')
    chapter(novel, 2, 'second')
    monkeypatch.setattr(diagnostics, 'MAX_TOTAL_BYTES', 6)
    report = diagnostics.inspect_manuscript(novel)
    assert not report['complete'] and len(report['chapters']) == 1


@pytest.mark.parametrize('limit,value', [('MAX_FILES', 1), ('MAX_PARAGRAPHS', 1)])
def test_global_limits_fail_explicitly(novel, monkeypatch, limit, value):
    chapter(novel, 1, 'a' * 50)
    chapter(novel, 2, 'b' * 50)
    monkeypatch.setattr(diagnostics, limit, value)
    with pytest.raises(ValueError, match='limit'):
        diagnostics.inspect_manuscript(novel)


def test_symlink_chapter_never_discloses_external_text(novel, tmp_path):
    outside = tmp_path / 'private.txt'
    outside.write_text('private' * 30, encoding='utf-8')
    try:
        (novel / 'chapters' / '000001.txt').symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation requires platform privileges')
    report = diagnostics.inspect_manuscript(novel, include_excerpts=True)
    assert not report['complete'] and report['chapters'] == []
    assert 'privateprivate' not in json.dumps(report)


def test_linked_chapter_directory_is_rejected(novel, tmp_path):
    (novel / 'chapters').rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (novel / 'chapters').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation requires platform privileges')
    with pytest.raises(ValueError, match='directory'):
        diagnostics.inspect_manuscript(novel)


def test_changed_during_read_is_not_counted(novel, monkeypatch):
    path = chapter(novel, 1, 'original')
    original = Path.open
    def changing_open(self, *args, **kwargs):
        if self == path and args and args[0] == 'rb':
            with original(self, 'wb') as stream:
                stream.write(b'changed contents')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', changing_open)
    report = diagnostics.inspect_manuscript(novel)
    assert not report['complete'] and report['chapters'] == []


def test_markdown_has_evidence_and_escapes_html(novel):
    for number in (1, 2):
        chapter(novel, number, '<script>alert("private")</script> ' * 3)
    report = diagnostics.inspect_manuscript(novel, include_excerpts=True)
    output = diagnostics.render_markdown(report)
    assert '<script>' not in output and '&lt;script&gt;' in output
    assert 'Chapter 2, line 1' in output
    assert '## Settings' in output and '## Limits' in output


def test_web_mcp_and_core_reports_match_and_exports_are_downloads(novel, monkeypatch):
    from ui import app as web
    import novel_server
    from fastapi import HTTPException

    chapter(novel, 1, 'Example words 汉字')
    def lookup(name):
        if name != 'Demo':
            raise HTTPException(404, 'Unknown project')
        return SimpleNamespace(path=novel)
    monkeypatch.setattr(web, 'get_novel_manager', lookup)
    monkeypatch.setattr(novel_server, 'nm', lambda: lookup('Demo'))
    expected = diagnostics.inspect_manuscript(novel, target_units=10)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://test') as client:
            prefix = '/api/novels/Demo/manuscript-report?target_units=10'
            response = await client.get(prefix)
            assert response.json()['report'] == expected
            assert response.headers['cache-control'] == 'no-store'
            exported = await client.get(prefix + '&format=json')
            assert exported.json() == expected
            assert 'attachment' in exported.headers['content-disposition']
            markdown = await client.get(prefix + '&format=markdown')
            assert markdown.text == diagnostics.render_markdown(expected)
            assert (await client.get(prefix + '&format=zip')).status_code == 400
            assert (await client.get(prefix + '&end_chapter=0')).status_code == 400
            assert (await client.get('/api/novels/Unknown/manuscript-report')).status_code == 404
        assert json.loads(await novel_server.inspect_manuscript(target_units=10)) == expected
        tools = await novel_server.list_tools()
        tool = next(t for t in tools if t.name == 'inspect_manuscript')
        assert 'include_excerpts' in tool.inputSchema['properties']
        assert novel_server.HANDLERS['inspect_manuscript'] is novel_server.inspect_manuscript
        assert 'inspect_manuscript' not in novel_server.GLOBAL_TOOLS
    asyncio.run(run())


def test_cli_registered_project_and_reports_protect_existing_files(novel, tmp_path, monkeypatch, capsys):
    import config
    import novel_cli
    from core import workspace_manager

    chapter(novel, 1, 'Hello world')
    monkeypatch.setattr(config, 'NOVELS_ROOT', novel.parent)
    monkeypatch.setattr(config, 'STORAGE_ROOT', novel.parent)
    monkeypatch.setattr(workspace_manager, 'WorkspaceManager', lambda *_: SimpleNamespace(data={'novels': {'Demo': {}}}))
    assert novel_cli.main(['inspect', '--novel', 'Demo', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == diagnostics.inspect_manuscript(novel)
    output = tmp_path / 'report.md'
    assert novel_cli.main(['inspect', '--novel', 'Demo', '--output', str(output)]) == 0
    assert output.read_text(encoding='utf-8').startswith('# Manuscript diagnostics')
    previous = output.read_bytes()
    assert novel_cli.main(['inspect', '--novel', 'Demo', '--output', str(output)]) == 1
    assert output.read_bytes() == previous
    assert novel_cli.main(['inspect', '--novel', 'Demo', '--output', str(novel / 'report.md')]) == 1
    assert not (novel / 'report.md').exists()
    assert novel_cli.main(['inspect', '--novel', '..']) == 1
    assert novel_cli.main(['inspect', '--novel', 'Unknown']) == 1
    chapter(novel, 2, '').write_bytes(b'\xff')
    assert novel_cli.main(['inspect', '--novel', 'Demo', '--json']) == 2
