import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

import config
import novel_cli
import core.workspace_manager as workspace_module
from core.foreshadow_manager import ForeshadowManager
from core.workspace_manager import WorkspaceManager


@pytest.fixture
def project(tmp_path, monkeypatch):
    storage = tmp_path / 'storage'
    storage.mkdir()
    for module in (config, workspace_module):
        monkeypatch.setattr(module, 'NOVELS_ROOT', storage / 'novels')
        monkeypatch.setattr(module, 'WORKSPACE_FILE', storage / 'workspace.json')
    monkeypatch.setattr(config, 'STORAGE_ROOT', storage)
    WorkspaceManager(logging.getLogger(__name__)).create_novel('Demo')
    manager = ForeshadowManager(storage / 'novels' / 'Demo', logging.getLogger(__name__))
    manager.create('Seed', tags=['Arc, Part 1'], notes='Private note')
    return manager


def payload(manager):
    item = manager.list()[0]
    return {'selection': [{'id': item['id'], 'expected_revision': item['revision']}], 'changes': {'target_delta': 3}}


def test_web_mcp_parity_dry_run_commit_export_and_unknown_project(project, monkeypatch):
    from ui import app as web
    from fastapi import HTTPException
    import novel_server
    def lookup(name):
        if name != 'Demo':
            raise HTTPException(404, 'Unknown project')
        return SimpleNamespace(path=project.path.parent, get_current_chapter=lambda: 5)
    monkeypatch.setattr(web, 'get_novel_manager', lookup)
    monkeypatch.setattr(novel_server, 'nm', lambda: lookup('Demo'))
    monkeypatch.setattr(novel_server, 'fsh_', lambda: project)

    async def run():
        before = project.path.read_bytes()
        request = payload(project)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://test') as client:
            prefix = '/api/novels/Demo'
            response = await client.post(prefix + '/foreshadow-batch', json=request)
            assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
            assert response.json()['result'] == json.loads(await novel_server.batch_update_foreshadows(**request))
            assert project.path.read_bytes() == before
            response = await client.post(prefix + '/foreshadow-batch', json={**request, 'dry_run': False})
            assert response.json()['result']['applied']
            stale = await client.post(prefix + '/foreshadow-batch', json={**request, 'dry_run': False})
            assert stale.status_code == 400
            response = await client.get(prefix + '/foreshadow-report?ownership=author')
            assert response.status_code == 200
            assert 'attachment' in response.headers['content-disposition']
            assert response.headers['cache-control'] == 'no-store'
            assert response.json() == json.loads(await novel_server.export_foreshadow_report(ownership='author'))
            assert 'Private note' not in response.text
            response = await client.get(prefix + '/foreshadow-report?format=markdown&include_notes=true')
            assert 'Private note' in response.text and response.headers['content-type'].startswith('text/markdown')
            for suffix in ('/foreshadow-report', '/foreshadow-board?ownership=author'):
                assert (await client.get('/api/novels/Unknown' + suffix)).status_code == 404
            assert (await client.post('/api/novels/Unknown/foreshadow-batch', json=request)).status_code == 404
        catalog = {tool.name: tool for tool in await novel_server.list_tools()}
        assert len(catalog) == 95
        for name in ('batch_update_foreshadows', 'export_foreshadow_report'):
            assert name in novel_server.HANDLERS and name not in novel_server.GLOBAL_TOOLS
        assert catalog['batch_update_foreshadows'].inputSchema['properties']['dry_run']['default'] is True
    asyncio.run(run())


def test_web_rejects_bad_batches_and_partial_downloads(project, monkeypatch):
    from ui import app as web
    monkeypatch.setattr(web, 'get_novel_manager', lambda _: SimpleNamespace(path=project.path.parent, get_current_chapter=lambda: 0))
    project.create('Another')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url='http://test') as client:
            for data in ([], {}, {'selection': [], 'changes': {}}, {**payload(project), 'dry_run': 'false'}):
                assert (await client.post('/api/novels/Demo/foreshadow-batch', json=data)).status_code == 400
            for query in ('max_items=1', 'format=html', 'ownership=bad', 'max_items=5001'):
                response = await client.get('/api/novels/Demo/foreshadow-report?' + query)
                assert response.status_code == 400 and 'content-disposition' not in response.headers
    asyncio.run(run())


def test_cli_filters_preview_explicit_apply_and_stale_error(project, tmp_path, capsys):
    assert novel_cli.main(['foreshadows', 'list', '--novel', 'Demo', '--ownership', 'author']) == 0
    board = json.loads(capsys.readouterr().out)
    assert board['total_matches'] == 1 and board['current_chapter'] == 0
    request = tmp_path / 'batch.json'
    request.write_text(json.dumps(payload(project)), encoding='utf-8')
    args = ['foreshadows', 'batch', '--novel', 'Demo', '--file', str(request)]
    before = project.path.read_bytes()
    assert novel_cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)['dry_run']
    assert project.path.read_bytes() == before
    assert novel_cli.main(args + ['--apply']) == 0
    assert json.loads(capsys.readouterr().out)['applied']
    assert novel_cli.main(args + ['--apply']) == 1
    assert 'changed' in json.loads(capsys.readouterr().err)['error']


def test_cli_export_new_file_protected_paths_and_partial_status(project, tmp_path, capsys):
    project.create('Another')
    args = ['foreshadows', 'export', '--novel', 'Demo']
    assert novel_cli.main(args + ['--max-items', '1']) == 2
    assert not json.loads(capsys.readouterr().out)['complete']
    target = tmp_path / 'report.md'
    assert novel_cli.main(args + ['--format', 'markdown', '--output', str(target)]) == 0
    body = target.read_text('utf-8')
    assert '# Foreshadow planning report' in body and 'Private note' not in body
    assert novel_cli.main(args + ['--output', str(target)]) == 1
    capsys.readouterr()
    assert target.read_text('utf-8') == body
    protected = config.STORAGE_ROOT / 'report.json'
    assert novel_cli.main(args + ['--output', str(protected)]) == 1
    assert 'outside runtime storage' in capsys.readouterr().err
    assert not protected.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ['report.md', 'storage']


@pytest.mark.parametrize('name', ['../Demo', 'Missing', 'C:\\Demo'])
def test_cli_requires_registered_contained_project(project, name, capsys):
    assert novel_cli.main(['foreshadows', 'list', '--novel', name]) == 1
    assert json.loads(capsys.readouterr().err)['success'] is False


@pytest.mark.parametrize('contents', ['[]', '{bad', '{"dry_run":false}', ' ' * (1024 * 1024 + 1)],
                         ids=['array', 'malformed-json', 'unexpected-keys', 'oversized'])
def test_cli_malformed_or_oversized_request_is_not_applied(project, tmp_path, capsys, contents):
    request = tmp_path / 'request.json'
    request.write_text(contents, encoding='utf-8')
    before = project.path.read_bytes()
    assert novel_cli.main(['foreshadows', 'batch', '--novel', 'Demo', '--file', str(request), '--apply']) == 1
    assert json.loads(capsys.readouterr().err)['success'] is False
    assert project.path.read_bytes() == before
