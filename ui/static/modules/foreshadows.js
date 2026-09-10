/* Author-managed foreshadow planning. IDs and prose are always escaped. */
(() => {
  const words = {
    en:{title:'Foreshadow planner',note:'Plan payoffs without rewriting chapters or calling AI. Editing makes a record author-managed: summary replay preserves it and will not resolve it automatically. The chapter cutoff is a planning clock, not historical state.',create:'Plant foreshadow',search:'Search text / notes',status:'Status',due:'Due window',priority:'Priority',tag:'Exact tag',clock:'Current chapter',window:'Upcoming chapters',apply:'Apply filters',all:'All',open:'Open',resolved:'Resolved',cancelled:'Cancelled',overdue:'Overdue',due_soon:'Due soon',scheduled:'Later',unplanned:'No valid target',closed:'Closed',high:'High',normal:'Normal',low:'Low',edit:'Edit / resolve',empty:'No matching foreshadows.',previous:'Previous',next:'Next',history:'Recent changes (up to 50)',evidence:'Evidence',notes:'Author notes',text:'Foreshadow text',introduced:'Introduced chapter (0 = planned)',target:'Target chapter',tags:'Tags (JSON array, up to 10; e.g. ["Plot"])',save:'Save changes',resolution:'Resolved chapter (optional)',resolutionNote:'Resolution note',managed:'Author-managed',automatic:'Summary-managed',matches:'Matches',remaining:'Chapters until target',conflict:'If another editor changes this record, saving is rejected. Refresh the board before retrying.',loading:'Loading…',cancel:'Close',introValue:'Introduced',targetValue:'Target',export:'Copy visible page as JSON',copied:'Visible page copied',invalid:'Invalid legacy records'},
    zh:{title:'伏笔规划台',note:'规划伏笔回收，不修改正文、不调用 AI。编辑后该记录由作者管理：摘要重建保留记录，并停止自动回收。当前章节只作为计划时钟，不代表历史快照。',create:'手动埋设伏笔',search:'搜索正文 / 备注',status:'状态',due:'到期范围',priority:'优先级',tag:'精确标签',clock:'当前章节',window:'未来章节窗口',apply:'应用筛选',all:'全部',open:'待回收',resolved:'已回收',cancelled:'已取消',overdue:'已逾期',due_soon:'即将到期',scheduled:'后续计划',unplanned:'无有效目标',closed:'已关闭',high:'高',normal:'普通',low:'低',edit:'编辑 / 回收',empty:'没有符合条件的伏笔。',previous:'上一页',next:'下一页',history:'最近变更（最多 50 条）',evidence:'证据',notes:'作者备注',text:'伏笔内容',introduced:'埋设章节（0 表示计划中）',target:'目标回收章节',tags:'标签（JSON 数组，最多 10 个；如 ["主线"]）',save:'保存修改',resolution:'实际回收章节（选填）',resolutionNote:'回收说明',managed:'作者管理',automatic:'摘要管理',matches:'匹配数量',remaining:'距目标章节',conflict:'其他编辑者修改后会拒绝保存；请刷新看板后重试。',loading:'加载中…',cancel:'关闭',introValue:'埋设',targetValue:'目标',export:'复制当前页 JSON',copied:'已复制当前页',invalid:'旧记录状态异常'},
    ja:{title:'伏線プランナー',note:'本文を書き換えず、AI を呼び出さずに回収を計画します。編集した記録は作者管理となり、再構築後も保持され、自動回収されません。現在章は計画用の時計であり、過去の状態ではありません。',create:'伏線を追加',search:'本文 / メモを検索',status:'状態',due:'期限',priority:'優先度',tag:'完全一致タグ',clock:'現在の章',window:'今後の章数',apply:'絞り込み',all:'すべて',open:'未回収',resolved:'回収済み',cancelled:'取消済み',overdue:'期限超過',due_soon:'まもなく期限',scheduled:'後の予定',unplanned:'有効な目標なし',closed:'終了',high:'高',normal:'標準',low:'低',edit:'編集 / 回収',empty:'一致する伏線はありません。',previous:'前へ',next:'次へ',history:'最近の変更（最大 50 件）',evidence:'根拠',notes:'作者メモ',text:'伏線の内容',introduced:'導入章（0 は計画中）',target:'回収予定章',tags:'タグ（JSON 配列、最大 10 個；例 ["本筋"]）',save:'変更を保存',resolution:'回収した章（任意）',resolutionNote:'回収メモ',managed:'作者管理',automatic:'要約管理',matches:'一致件数',remaining:'目標までの章数',conflict:'他の編集者が変更した場合は保存が拒否されます。更新してから再試行してください。',loading:'読み込み中…',cancel:'閉じる',introValue:'導入',targetValue:'目標',export:'表示ページの JSON をコピー',copied:'表示ページをコピーしました',invalid:'旧記録の不正な状態'}
  };
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tFor=language=>words[language]||words.en;
  function boardURL(novel,filters={}) {
    const query=new URLSearchParams();
    for(const [key,value] of Object.entries(filters)) if(value!=='' && value!==null && value!==undefined) query.set(key,String(value));
    return `/api/novels/${encodeURIComponent(novel)}/foreshadow-board?${query}`;
  }
  function renderBoard(board,language='en') {
    const t=tFor(language);
    return `<div class="foreshadow-metrics">${['open','overdue','due_soon','resolved','cancelled'].map(k=>`<div><b>${esc(board.summary[k])}</b><span>${esc(t[k])}</span></div>`).join('')}</div>${board.summary.invalid?`<p>${esc(t.invalid)}: ${esc(board.summary.invalid)}</p>`:''}
      <p>${esc(t.clock)}: ${esc(board.current_chapter??0)} · ${esc(t.matches)}: ${esc(board.total_matches)} · ${esc(board.items.length?board.offset+1:0)}–${esc(board.items.length?board.offset+board.items.length:0)}</p>
      <div class="foreshadow-cards">${board.items.map((item,index)=>`<article class="foreshadow-card"><header><span class="foreshadow-badge ${esc(item.due_state)}">${esc(t[item.due_state]||item.due_state)}</span> <span>${esc(t[item.priority]||item.priority)} · ${esc(t[item.status]||item.status)}</span></header><h3>${esc(item.text)}</h3><p>${esc(t.introValue)}: ${esc(item.introduced_chapter??'?')} · ${esc(t.targetValue)}: ${esc(item.target_chapter??'?')} · ${esc(t.remaining)}: ${esc(item.remaining_chapters??'—')}</p><p>${(item.tags||[]).map(tag=>`<span class="foreshadow-tag">${esc(tag)}</span>`).join(' ')}</p><p>${esc(t[item.author_managed?'managed':'automatic'])}</p>${item.notes?`<p class="foreshadow-prose">${esc(item.notes)}</p>`:''}<button type="button" class="secondary" data-edit="${index}" ${item.id?'':'disabled'}>${esc(t.edit)}</button><details><summary>${esc(t.history)}</summary><pre>${esc(JSON.stringify(item.history||[],null,2))}</pre></details>${item.evidence?`<details><summary>${esc(t.evidence)}</summary><p>${esc(item.evidence)}</p></details>`:''}${item.status==='resolved'?`<p>${esc(t.resolution)}: ${esc(item.resolved_chapter??'—')}</p><p>${esc(item.resolution_note||'')}</p>`:''}</article>`).join('')||`<p>${esc(t.empty)}</p>`}</div>
      <div class="foreshadow-pagination"><button type="button" data-offset="${Math.max(0,board.offset-board.limit)}" ${board.offset===0?'disabled':''}>${esc(t.previous)}</button><button type="button" data-offset="${board.offset+board.limit}" ${board.has_more?'':'disabled'}>${esc(t.next)}</button></div>`;
  }
  function formatTags(tags) { return JSON.stringify(tags); }
  function parseTags(value) {
    const tags=value.trim() ? JSON.parse(value) : [];
    if(!Array.isArray(tags) || tags.length>10 || tags.some(tag=>typeof tag!=='string'||!tag.trim()||tag.length>40))
      throw new Error('Tags must be a JSON array of up to 10 nonempty strings (40 characters each).');
    return tags;
  }
  function editValues(form,existing=false) {
    const data={text:form.text, target_chapter:Number(form.target_chapter),priority:form.priority,
      tags:parseTags(form.tags),notes:form.notes};
    if(existing) {
      data.status=form.status;
      if(data.status==='resolved') {
        data.resolved_chapter=form.resolved_chapter===''?null:Number(form.resolved_chapter);
        data.resolution_note=form.resolution_note;
      }
    } else data.introduced_chapter=Number(form.introduced_chapter);
    return data;
  }
  if(typeof module!=='undefined') module.exports={renderBoard,boardURL,editValues,formatTags,parseTags};
  if(typeof document==='undefined') return;
  let generation=0;
  const language=()=>localStorage.getItem('novel-ui-language')||'en';
  const select=(name,label,values,selected,t)=>`<label>${esc(label)}<select name="${name}">${values.map(value=>`<option value="${value}" ${value===selected?'selected':''}>${esc(t[value]||value)}</option>`).join('')}</select></label>`;
  const input=(name,label,value,type='text',extra='')=>`<label>${esc(label)}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
  async function open() {
    const novel=state.novel;if(!novel)return;
    const id=++generation, lang=language(),t=tFor(lang),dialog=ensureUtilityDialog('foreshadowPlannerDialog',t.title);
    dialog.querySelector('h2').textContent=t.title;
    dialog.querySelector('.dialog-x').formNoValidate=true;
    dialog.querySelector('.utility-dialog-body').innerHTML=`<p>${esc(t.note)}</p><div class="foreshadow-filters">${input('query',t.search,'','text','maxlength="200"')}${select('status',t.status,['all','open','resolved','cancelled'],'all',t)}${select('due',t.due,['all','overdue','due_soon','scheduled','unplanned','closed'],'all',t)}${select('priority',t.priority,['all','high','normal','low'],'all',t)}${input('tag',t.tag,'','text','maxlength="40"')}${input('current_chapter',t.clock,'','number','min="0" max="1000000" step="1"')}${input('due_within',t.window,5,'number','required min="0" max="1000000" step="1"')}</div><div class="foreshadow-actions"><button type="button" class="primary" id="filterForeshadows">${esc(t.apply)}</button><button type="button" class="secondary" id="createForeshadow">${esc(t.create)}</button><button type="button" class="secondary" id="copyForeshadows" disabled>${esc(t.export)}</button></div><div id="foreshadowBoardResults" aria-live="polite"></div>`;
    dialog.showModal();
    const host=$('foreshadowBoardResults'),filter=$('filterForeshadows'),copy=$('copyForeshadows');
    const active=()=>generation===id && dialog.open && state.novel===novel;
    let sequence=0,editorSequence=0,lastFilters={},board=null,changed=false;
    dialog.onclose=()=>{if(changed&&state.novel===novel&&typeof loadTimeline==='function')loadTimeline().catch(error=>toast(error.message,true));};
    async function refresh(offset=0,readFilters=true) {
      if(readFilters) {
        const values={};
        for(const control of dialog.querySelectorAll('.foreshadow-filters [name]')) {
          if(!control.reportValidity())return;
          values[control.name]=control.value;
        }
        lastFilters=values;
      }
      const ticket=++sequence;filter.disabled=true;copy.disabled=true;host.textContent=t.loading;
      try {
        const result=await json(boardURL(novel,{...lastFilters,offset,limit:20}));
        if(!active()||sequence!==ticket)return;
        board=result.board;host.innerHTML=renderBoard(board,lang);
        host.querySelectorAll('[data-offset]').forEach(button=>button.onclick=()=>refresh(Number(button.dataset.offset),false));
        host.querySelectorAll('[data-edit]').forEach(button=>button.onclick=()=>edit(board.items[Number(button.dataset.edit)]));
        copy.disabled=false;
      }catch(error){if(active()&&sequence===ticket)host.textContent=error.message;}
      finally {if(active()&&sequence===ticket)filter.disabled=false;}
    }
    async function edit(item=null) {
      const editTicket=++editorSequence;
      const editor=ensureUtilityDialog('foreshadowEditorDialog',item?t.edit:t.create),old=item||{};
      editor.querySelector('h2').textContent=item?t.edit:t.create;
      editor.querySelector('.dialog-x').formNoValidate=true;
      const area=(name,label,value,max)=>`<label>${esc(label)}<textarea name="${name}" maxlength="${max}" ${name==='text'?'required':''}>${esc(value)}</textarea></label>`;
      editor.querySelector('.utility-dialog-body').innerHTML=`<p>${esc(t.note)}</p>${area('text',t.text,old.text||'',2000)}<div class="foreshadow-filters">${item?'':input('introduced_chapter',t.introduced,0,'number','required min="0" max="1000000" step="1"')}${input('target_chapter',t.target,old.target_chapter??((board?.current_chapter||0)+10),'number','required min="1" max="1000001" step="1"')}${select('priority',t.priority,['high','normal','low'],old.priority||'normal',t)}${item?select('status',t.status,['open','resolved','cancelled'],old.status,t):''}${input('tags',t.tags,formatTags(old.tags||[]))}</div>${area('notes',t.notes,old.notes||'',4000)}${item?`<div id="foreshadowResolutionFields">${input('resolved_chapter',t.resolution,old.resolved_chapter??'','number','min="1" max="1000001" step="1"')}${area('resolution_note',t.resolutionNote,old.resolution_note||'',2000)}</div>`:''}<p>${esc(t.conflict)}</p><button type="button" class="primary" id="saveForeshadowRecord">${esc(t.save)}</button><p id="foreshadowEditorError" role="alert"></p>`;
      const status=editor.querySelector('[name="status"]'),resolution=editor.querySelector('#foreshadowResolutionFields');
      const visibility=()=>{if(resolution){resolution.hidden=status.value!=='resolved';resolution.querySelectorAll('[name]').forEach(c=>c.disabled=resolution.hidden);}};
      if(status)status.onchange=visibility;visibility();
      editor.showModal();
      const save=$('saveForeshadowRecord');
      save.onclick=async()=>{
        if(!active()){editor.close();return;}
        const form={};
        for(const control of editor.querySelectorAll('[name]')){
          if(!control.disabled&&!control.reportValidity())return;
          form[control.name]=control.value;
        }
        save.disabled=true;
        try {
          const data=editValues(form,Boolean(item));
          if(item)data.expected_revision=item.revision;
          await json(`/api/novels/${enc(novel)}/foreshadowing${item?'/'+enc(item.id):''}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
          changed=true;if(editorSequence===editTicket)editor.close();if(active())await refresh(0,false);
        }catch(error){if(editor.open&&editorSequence===editTicket)$('foreshadowEditorError').textContent=error.message;}
        finally {save.disabled=false;}
      };
      editor.querySelector('form').onsubmit=event=>{if(event.submitter?.classList.contains('dialog-x'))return;event.preventDefault();if(!save.disabled)save.click();};
    }
    filter.onclick=()=>refresh();$('createForeshadow').onclick=()=>edit();
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(board,null,2));toast(t.copied);}catch(error){toast(error.message,true);}};
    dialog.querySelector('.foreshadow-filters').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();if(!filter.disabled)refresh();}};
    await refresh();
  }
  window.NovelForeshadows={open};
  document.addEventListener('DOMContentLoaded',()=>{
    const anchor=$('checkConsistency');if(!anchor)return;
    const button=document.createElement('button');button.id='foreshadowPlannerButton';button.type='button';button.className='secondary';
    const update=()=>{button.textContent=tFor(language()).title;};update();button.onclick=open;anchor.before(button);
    $('languageSelect')?.addEventListener('change',update);
  });
})();
