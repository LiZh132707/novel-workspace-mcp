/* Inspect and download existing project backups without restoring over a novel. */
(() => {
  const labels = {
    en:{title:'Project backups',create:'Create backup now',verify:'Verify integrity',download:'Download ZIP',empty:'No backups found.',note:'Verification checks CRC, archive paths and project state without extracting files. A pass is not a guarantee of story completeness. Download a copy before using Import novel / project to restore it as a separate project.'},
    zh:{title:'项目备份',create:'立即创建备份',verify:'校验完整性',download:'下载 ZIP',empty:'暂无备份。',note:'校验会检查 CRC、归档路径和项目状态，不解压文件。通过校验不代表故事内容完整。可先下载副本，再通过导入功能恢复为独立项目。'},
    ja:{title:'プロジェクトのバックアップ',create:'今すぐバックアップ',verify:'整合性を検証',download:'ZIP をダウンロード',empty:'バックアップはありません。',note:'展開せずに CRC、パス、状態ファイルを検証します。物語の完全性を保証するものではありません。コピーをダウンロードし、インポート機能で別のプロジェクトとして復元できます。'}
  };
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function renderBackups(items, novel, language='en') {
    const t=labels[language]||labels.en;
    return items.map((item,index)=>`<article class="list-card" style="padding:14px;margin:10px 0"><b>${escape(item.name)}</b><p>${escape(item.created_at)} · ${escape(item.size_bytes)} bytes</p><button type="button" class="secondary backup-verify" data-index="${index}">${escape(t.verify)}</button> <a href="/api/novels/${encodeURIComponent(novel)}/backups/${encodeURIComponent(item.name)}/download" download>${escape(t.download)}</a><pre class="backup-result" aria-live="polite" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></article>`).join('') || `<p>${escape(t.empty)}</p>`;
  }
  if(typeof module!=='undefined') module.exports={renderBackups};
  if(typeof document==='undefined') return;
  let opening=0;
  async function openBackups() {
    const novel=state.novel;
    if(!novel) return;
    const id=++opening, language=localStorage.getItem('novel-ui-language')||'en', t=labels[language]||labels.en;
    const dialog=ensureUtilityDialog('projectBackupsDialog',t.title);
    dialog.querySelector('h2').textContent=t.title;
    dialog.querySelector('.utility-dialog-body').innerHTML=`<p class="hint">${escape(t.note)}</p><button type="button" class="primary" id="createProjectBackup">${escape(t.create)}</button><div id="projectBackupList" aria-live="polite"></div>`;
    dialog.showModal();
    const host=$('projectBackupList'), create=$('createProjectBackup');
    const active=()=>opening===id && dialog.open && state.novel===novel;
    async function refresh() {
      const data=await json(`/api/novels/${enc(novel)}/backups`);
      if(!active()) return;
      host.innerHTML=renderBackups(data.backups,novel,language);
      host.querySelectorAll('.backup-verify').forEach(button=>button.onclick=async()=>{
        button.disabled=true;
        try {
          const item=data.backups[Number(button.dataset.index)];
          const report=await json(`/api/novels/${enc(novel)}/backups/${enc(item.name)}/verify`,{method:'POST'});
          if(active()) button.parentElement.querySelector('.backup-result').textContent=JSON.stringify(report.verification,null,2);
        } catch(error) {if(active()) toast(error.message,true);}
        finally {button.disabled=false;}
      });
    }
    create.onclick=async()=>{
      create.disabled=true;
      try {await json(`/api/novels/${enc(novel)}/backups`,{method:'POST'});if(active()) await refresh();}
      catch(error) {if(active()) toast(error.message,true);}
      finally {create.disabled=false;}
    };
    try {await refresh();} catch(error) {if(active()) host.textContent=error.message;}
  }
  document.addEventListener('DOMContentLoaded',()=>{
    const anchor=$('globalSearchBtn');
    if(!anchor) return;
    const button=document.createElement('button');
    button.id='projectBackupsButton';button.type='button';button.className='secondary';
    const update=()=>{button.textContent=(labels[localStorage.getItem('novel-ui-language')||'en']||labels.en).title;};
    update();button.onclick=openBackups;anchor.before(button);
    $('languageSelect')?.addEventListener('change',update);
  });
})();
