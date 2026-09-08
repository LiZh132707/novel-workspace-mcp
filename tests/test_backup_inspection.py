import logging
import zipfile
import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from core.backup_manager import BackupScheduler


@pytest.fixture
def scheduler(tmp_path):
    return BackupScheduler(tmp_path / "novels", tmp_path, logging.getLogger("backup-inspection"))


def archive(scheduler, name, state="{}"):
    scheduler.output.mkdir(parents=True, exist_ok=True)
    path = scheduler.output / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("state.json", state)
        z.writestr("chapters/0001.txt", "Example chapter")
    return path


def test_zero_retention_is_rejected_before_it_can_delete_new_backup(tmp_path):
    with pytest.raises(ValueError):
        BackupScheduler(tmp_path, tmp_path, logging.getLogger("test"), keep_per_novel=0)


def test_latest_backup_is_chronological_across_project_names(scheduler):
    archive(scheduler, "Zebra_20260101_000000_000000.zip")
    latest = archive(scheduler, "Alpha_20260908_000000_000000.zip")
    assert scheduler.status()["latest"] == latest.name


def test_invalid_project_state_does_not_produce_backup(scheduler):
    novel = scheduler.novels_root / "Broken"
    novel.mkdir(parents=True)
    (novel / "state.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        scheduler.create(novel)
    assert not list(scheduler.output.glob("*.zip"))


def test_inventory_matches_exact_project_and_ignores_unmanaged_files(scheduler):
    short=archive(scheduler,"Story_20260908_000000_000000.zip")
    archive(scheduler,"Story_extra_20260908_000000_000000.zip")
    archive(scheduler,"Story_20269999_000000_000000.zip")
    (scheduler.output/'notes.zip').write_bytes(b'not managed')
    items=scheduler.list_backups('Story')
    assert [item['name'] for item in items] == [short.name]
    assert items[0]['size_bytes'] == short.stat().st_size


def test_verify_is_read_only_and_reports_reproducible_hash(scheduler):
    path=archive(scheduler,'Demo_20260908_000000_000000.zip')
    before=path.read_bytes()
    report=scheduler.verify(path.name,'Demo')
    assert report['status']=='pass'
    assert report['file_count']==2
    assert report['sha256']==hashlib.sha256(before).hexdigest()
    assert report['size_bytes']==len(before)
    assert path.read_bytes()==before
    assert not scheduler.novels_root.exists()


def test_created_backup_can_be_imported_as_a_separate_project(scheduler,tmp_path):
    from core.data_portability import ProjectZipRestorer

    root=scheduler.novels_root/'Demo'
    (root/'chapters').mkdir(parents=True)
    (root/'state.json').write_text('{"current_chapter":1}',encoding='utf-8')
    (root/'chapters'/'0001.txt').write_text('Original chapter',encoding='utf-8')
    backup=scheduler.create(root)
    restored=tmp_path/'restored'
    ProjectZipRestorer.extract(backup,restored)
    assert (restored/'chapters'/'0001.txt').read_text('utf-8')=='Original chapter'
    assert (root/'state.json').read_bytes()==(restored/'state.json').read_bytes()


@pytest.mark.parametrize('state',['[]','not json','null'])
def test_verification_reports_bad_project_state(scheduler,state):
    path=archive(scheduler,'Demo_20260908_000000_000000.zip',state)
    report=scheduler.verify(path.name)
    assert report['status']=='fail' and report['errors']


@pytest.mark.parametrize('member',['../outside.txt','/absolute.txt','dir\\outside.txt','C:/outside.txt','STATE.JSON'])
def test_verification_rejects_unsafe_or_duplicate_paths(scheduler,member):
    path=archive(scheduler,'Demo_20260908_000000_000000.zip')
    with zipfile.ZipFile(path,'a') as z:
        info=zipfile.ZipInfo('placeholder')
        info.filename=member
        z.writestr(info,'extra')
    assert scheduler.verify(path.name)['status']=='fail'


def test_verification_detects_crc_corruption(scheduler):
    path=archive(scheduler,'Demo_20260908_000000_000000.zip')
    path.write_bytes(path.read_bytes().replace(b'Example chapter',b'Damaged chapter'))
    assert scheduler.verify(path.name)['status']=='fail'


def test_backup_resolution_rejects_other_projects_and_traversal(scheduler):
    path=archive(scheduler,'Demo_20260908_000000_000000.zip')
    for name,novel in [(path.name,'Other'),('../'+path.name,None),('..\\'+path.name,None),('Missing_20260908_000000_000000.zip',None)]:
        with pytest.raises(ValueError):
            scheduler.resolve_backup(name,novel)


def test_existing_good_backup_survives_failed_creation(scheduler):
    old=archive(scheduler,'Demo_20260901_000000_000000.zip')
    scheduler.keep_per_novel=1
    root=scheduler.novels_root/'Demo'
    root.mkdir(parents=True)
    (root/'state.json').write_text('broken',encoding='utf-8')
    with pytest.raises(ValueError):
        scheduler.create(root)
    assert old.exists()
    assert not list(scheduler.output.glob('*.tmp'))


def test_cli_list_and_verify_do_not_create_archives(scheduler,monkeypatch,capsys):
    import config
    import novel_cli
    monkeypatch.setattr(config,'NOVELS_ROOT',scheduler.novels_root)
    monkeypatch.setattr(config,'STORAGE_ROOT',scheduler.output.parent)
    path=archive(scheduler,'Demo_20260908_000000_000000.zip')
    assert novel_cli.main(['backup','--list','--novel','Demo','--json'])==0
    assert json.loads(capsys.readouterr().out)['count']==1
    assert novel_cli.main(['backup','--verify',path.name,'--json'])==0
    assert json.loads(capsys.readouterr().out)['status']=='pass'
    path.write_bytes(b'broken archive')
    assert novel_cli.main(['backup','--verify',path.name,'--json'])==1
    assert json.loads(capsys.readouterr().out)['status']=='fail'
    assert len(list(scheduler.output.glob('*.zip')))==1


def test_web_backup_inventory_verification_download_and_project_isolation(scheduler,monkeypatch):
    from ui import app as web
    from fastapi import HTTPException
    path=archive(scheduler,'Demo_20260908_000000_000000.zip')
    other=archive(scheduler,'Other_20260908_000000_000000.zip')
    root=scheduler.novels_root/'Demo'
    root.mkdir(parents=True)
    (root/'state.json').write_text('{}',encoding='utf-8')
    def lookup(name):
        if name!='Demo':
            raise HTTPException(404,'Unknown project')
        return SimpleNamespace(path=root)
    monkeypatch.setattr(web,'get_novel_manager',lookup)
    monkeypatch.setattr(web,'backup_scheduler',scheduler)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url='http://test') as client:
            prefix='/api/novels/Demo/backups'
            listing=(await client.get(prefix)).json()
            assert [item['name'] for item in listing['backups']]==[path.name]
            verified=(await client.post(f'{prefix}/{path.name}/verify')).json()
            assert verified['verification']['status']=='pass'
            downloaded=await client.get(f'{prefix}/{path.name}/download')
            assert downloaded.content==path.read_bytes()
            assert downloaded.headers['content-type']=='application/zip'
            assert (await client.get(f'{prefix}/{other.name}/download')).status_code==404
            assert (await client.post(f'{prefix}/{other.name}/verify')).status_code==400
            assert (await client.get('/api/novels/Unknown/backups')).status_code==404
            created=await client.post(prefix)
            assert created.status_code==200 and created.json()['success']
            assert scheduler.verify(created.json()['name'],'Demo')['status']=='pass'
    asyncio.run(run())
