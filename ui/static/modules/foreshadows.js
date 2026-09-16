/* Author-managed foreshadow planning. IDs and prose are always escaped. */
(() => {
  const words = {
    en:{title:'Foreshadow planner',note:'Plan payoffs without rewriting chapters or calling AI. Editing makes a record author-managed: summary replay preserves it and will not resolve it automatically. The chapter cutoff is a planning clock, not historical state.',create:'Plant foreshadow',search:'Search text / notes',status:'Status',due:'Due window',priority:'Priority',tag:'Exact tag',clock:'Current chapter',window:'Upcoming chapters',apply:'Apply filters',all:'All',open:'Open',resolved:'Resolved',cancelled:'Cancelled',overdue:'Overdue',due_soon:'Due soon',scheduled:'Later',unplanned:'No valid target',closed:'Closed',high:'High',normal:'Normal',low:'Low',edit:'Edit / resolve',empty:'No matching foreshadows.',previous:'Previous',next:'Next',history:'Recent changes (up to 50)',evidence:'Evidence',notes:'Author notes',text:'Foreshadow text',introduced:'Introduced chapter (0 = planned)',target:'Target chapter',tags:'Tags (JSON array, up to 10; e.g. ["Plot"])',save:'Save changes',resolution:'Resolved chapter (optional)',resolutionNote:'Resolution note',managed:'Author-managed',automatic:'Summary-managed',matches:'Matches',remaining:'Chapters until target',conflict:'If another editor changes this record, saving is rejected. Refresh the board before retrying.',loading:'Loading…',cancel:'Close',introValue:'Introduced',targetValue:'Target',export:'Copy visible page as JSON',copied:'Visible page copied',invalid:'Invalid legacy records'},
    zh:{title:'伏笔规划台',note:'规划伏笔回收，不修改正文、不调用 AI。编辑后该记录由作者管理：摘要重建保留记录，并停止自动回收。当前章节只作为计划时钟，不代表历史快照。',create:'手动埋设伏笔',search:'搜索正文 / 备注',status:'状态',due:'到期范围',priority:'优先级',tag:'精确标签',clock:'当前章节',window:'未来章节窗口',apply:'应用筛选',all:'全部',open:'待回收',resolved:'已回收',cancelled:'已取消',overdue:'已逾期',due_soon:'即将到期',scheduled:'后续计划',unplanned:'无有效目标',closed:'已关闭',high:'高',normal:'普通',low:'低',edit:'编辑 / 回收',empty:'没有符合条件的伏笔。',previous:'上一页',next:'下一页',history:'最近变更（最多 50 条）',evidence:'证据',notes:'作者备注',text:'伏笔内容',introduced:'埋设章节（0 表示计划中）',target:'目标回收章节',tags:'标签（JSON 数组，最多 10 个；如 ["主线"]）',save:'保存修改',resolution:'实际回收章节（选填）',resolutionNote:'回收说明',managed:'作者管理',automatic:'摘要管理',matches:'匹配数量',remaining:'距目标章节',conflict:'其他编辑者修改后会拒绝保存；请刷新看板后重试。',loading:'加载中…',cancel:'关闭',introValue:'埋设',targetValue:'目标',export:'复制当前页 JSON',copied:'已复制当前页',invalid:'旧记录状态异常'},
    ja:{title:'伏線プランナー',note:'本文を書き換えず、AI を呼び出さずに回収を計画します。編集した記録は作者管理となり、再構築後も保持され、自動回収されません。現在章は計画用の時計であり、過去の状態ではありません。',create:'伏線を追加',search:'本文 / メモを検索',status:'状態',due:'期限',priority:'優先度',tag:'完全一致タグ',clock:'現在の章',window:'今後の章数',apply:'絞り込み',all:'すべて',open:'未回収',resolved:'回収済み',cancelled:'取消済み',overdue:'期限超過',due_soon:'まもなく期限',scheduled:'後の予定',unplanned:'有効な目標なし',closed:'終了',high:'高',normal:'標準',low:'低',edit:'編集 / 回収',empty:'一致する伏線はありません。',previous:'前へ',next:'次へ',history:'最近の変更（最大 50 件）',evidence:'根拠',notes:'作者メモ',text:'伏線の内容',introduced:'導入章（0 は計画中）',target:'回収予定章',tags:'タグ（JSON 配列、最大 10 個；例 ["本筋"]）',save:'変更を保存',resolution:'回収した章（任意）',resolutionNote:'回収メモ',managed:'作者管理',automatic:'要約管理',matches:'一致件数',remaining:'目標までの章数',conflict:'他の編集者が変更した場合は保存が拒否されます。更新してから再試行してください。',loading:'読み込み中…',cancel:'閉じる',introValue:'導入',targetValue:'目標',export:'表示ページの JSON をコピー',copied:'表示ページをコピーしました',invalid:'旧記録の不正な状態'}
  };
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tFor=language=>words[language]||words.en;
  const additions={
    en:{ownership:'Ownership',author:'Author-managed',summary:'Summary-managed',focus:'Quick view',upcoming:'Upcoming payoffs',urgent:'High-priority open',mine:'Author plans',batch:'Batch edit selected',selectPage:'Select this page',selected:'selected',batchNote:'Selection is limited to this page and clears on refresh. Preview first; apply is all-or-nothing. Every edited record becomes author-managed.',keep:'Unchanged',shift:'Shift by chapters',set:'Set target chapter',targetMode:'Deadline operation',targetValueBatch:'Chapter / delta',addTags:'Add tags (JSON array)',removeTags:'Remove tags (JSON array)',preview:'Preview changes',commit:'Apply reviewed changes',before:'Before',after:'After',field:'Field',changed:'records will change',noChanges:'No changes needed.',download:'Download all matches',notesOpt:'Include private notes / evidence',exportNote:'Exports use applied filters, not the selection (up to 5000 records). Story text and tags remain private even without notes.',resolutionMode:'Resolution chapter',setResolution:'Set actual chapter',clearResolution:'Clear actual chapter'},
    zh:{ownership:'管理方式',author:'作者管理',summary:'摘要管理',focus:'快捷视图',upcoming:'即将回收',urgent:'高优先级待回收',mine:'作者计划',batch:'批量编辑所选',selectPage:'选择当前页',selected:'项已选',batchNote:'仅选择当前页，刷新后清空。先预览再应用；任一记录冲突会取消整批操作。编辑后由作者管理。',keep:'不修改',shift:'按章节数顺延',set:'设置目标章节',targetMode:'目标调整方式',targetValueBatch:'章节 / 增减量',addTags:'添加标签（JSON 数组）',removeTags:'移除标签（JSON 数组）',preview:'预览变更',commit:'应用已预览的变更',before:'修改前',after:'修改后',field:'字段',changed:'条记录将修改',noChanges:'无需修改。',download:'下载全部匹配结果',notesOpt:'包含私人备注 / 证据',exportNote:'导出使用已应用的筛选，而不是所选记录（最多 5000 条）。不含备注时，正文和标签仍可能涉及私密内容。',resolutionMode:'实际回收章节',setResolution:'设置实际章节',clearResolution:'清空实际章节'},
    ja:{ownership:'管理方式',author:'作者管理',summary:'要約管理',focus:'クイック表示',upcoming:'回収間近',urgent:'優先度の高い未回収',mine:'作者の計画',batch:'選択を一括編集',selectPage:'このページを選択',selected:'件を選択',batchNote:'選択は現在のページのみで、更新時に解除されます。プレビュー後に一括適用します。競合時は全件中止し、編集後は作者管理になります。',keep:'変更なし',shift:'章数で移動',set:'目標章を設定',targetMode:'期限の操作',targetValueBatch:'章 / 増減数',addTags:'タグを追加（JSON 配列）',removeTags:'タグを削除（JSON 配列）',preview:'変更をプレビュー',commit:'確認した変更を適用',before:'変更前',after:'変更後',field:'項目',changed:'件を変更予定',noChanges:'変更はありません。',download:'一致する全件をダウンロード',notesOpt:'非公開メモ / 根拠を含める',exportNote:'適用済みの条件で出力します（最大 5000 件）。選択範囲とは別です。メモを除いても本文とタグに非公開情報が含まれる場合があります。',resolutionMode:'実際の回収章',setResolution:'回収章を設定',clearResolution:'回収章を消去'}
  };
  for(const lang of Object.keys(additions))Object.assign(words[lang],additions[lang]);
  words.en.choose='Select';words.zh.choose='选择';words.ja.choose='選択';
  function focusFilters(view) {
    const filters={status:'all',due:'all',priority:'all',ownership:'all',query:'',tag:''};
    if(view==='overdue')Object.assign(filters,{status:'open',due:'overdue'});
    if(view==='upcoming')Object.assign(filters,{status:'open',due:'due_soon'});
    if(view==='urgent')Object.assign(filters,{status:'open',priority:'high'});
    if(view==='mine')filters.ownership='author';
    return filters;
  }
  function reportURL(novel,filters={},format='json',includeNotes=false) {
    return boardURL(novel,{...filters,offset:undefined,limit:undefined,format,include_notes:includeNotes,max_items:5000}).replace('/foreshadow-board?','/foreshadow-report?');
  }
  function renderReportLinks(novel) {
    return [['json','JSON','json'],['markdown','Markdown','md']].map(([format,label,extension])=>`<a class="foreshadow-download" href="${esc(reportURL(novel,{},format))}" data-report="${format}" aria-disabled="true" tabindex="-1" target="_blank" rel="noopener" download="foreshadow-report.${extension}">${label}</a>`).join('');
  }
  function batchValues(form) {
    const values={};
    const integer=value=>{const n=Number(value);if(String(value).trim()===''||!Number.isSafeInteger(n))throw new Error('An integer chapter value is required.');return n;};
    if(form.target_mode==='shift')values.target_delta=integer(form.target_value);
    else if(form.target_mode==='set')values.target_chapter=integer(form.target_value);
    for(const key of ['status','priority'])if(form[key]&&form[key]!=='keep')values[key]=form[key];
    for(const key of ['add_tags','remove_tags'])if(form[key]?.trim())values[key]=parseTags(form[key]);
    if(form.resolution_mode==='setResolution')values.resolved_chapter=integer(form.resolved_chapter);
    if(form.resolution_mode==='clearResolution')values.resolved_chapter=null;
    if(form.resolution_note?.trim())values.resolution_note=form.resolution_note;
    if(!Object.keys(values).length)throw new Error('Choose at least one change.');
    return values;
  }
  function renderBatchPreview(result,language='en') {
    const t=tFor(language);
    return `<p>${esc(result.changed)} ${esc(t.changed)}</p>`+result.items.map(item=>`<details open><summary>${esc(item.text)}</summary>${Object.keys(item.changes).length?`<table class="foreshadow-diff"><thead><tr><th>${esc(t.field)}</th><th>${esc(t.before)}</th><th>${esc(t.after)}</th></tr></thead><tbody>${Object.entries(item.changes).map(([key,value])=>`<tr><th>${esc(key)}</th><td>${esc(JSON.stringify(value.before)??'—')}</td><td>${esc(JSON.stringify(value.after)??'—')}</td></tr>`).join('')}</tbody></table>`:`<p>${esc(t.noChanges)}</p>`}</details>`).join('');
  }
  function boardURL(novel,filters={}) {
    const query=new URLSearchParams();
    for(const [key,value] of Object.entries(filters)) if(value!=='' && value!==null && value!==undefined) query.set(key,String(value));
    return `/api/novels/${encodeURIComponent(novel)}/foreshadow-board?${query}`;
  }
  function renderBoard(board,language='en') {
    const t=tFor(language);
    return `<div class="foreshadow-metrics">${['open','overdue','due_soon','resolved','cancelled'].map(k=>`<div><b>${esc(board.summary[k])}</b><span>${esc(t[k])}</span></div>`).join('')}</div>${board.summary.invalid?`<p>${esc(t.invalid)}: ${esc(board.summary.invalid)}</p>`:''}
      <p>${esc(t.clock)}: ${esc(board.current_chapter??0)} · ${esc(t.matches)}: ${esc(board.total_matches)} · ${esc(board.items.length?board.offset+1:0)}–${esc(board.items.length?board.offset+board.items.length:0)}</p>
      <div class="foreshadow-cards">${board.items.map((item,index)=>`<article class="foreshadow-card"><label class="foreshadow-select"><input type="checkbox" data-select="${index}" aria-label="${esc(t.choose)}: ${esc(item.text)}"> ${esc(t.choose)}</label><header><span class="foreshadow-badge ${esc(item.due_state)}">${esc(t[item.due_state]||item.due_state)}</span> <span>${esc(t[item.priority]||item.priority)} · ${esc(t[item.status]||item.status)}</span></header><h3>${esc(item.text)}</h3><p>${esc(t.introValue)}: ${esc(item.introduced_chapter??'?')} · ${esc(t.targetValue)}: ${esc(item.target_chapter??'?')} · ${esc(t.remaining)}: ${esc(item.remaining_chapters??'—')}</p><p>${(item.tags||[]).map(tag=>`<span class="foreshadow-tag">${esc(tag)}</span>`).join(' ')}</p><p>${esc(t[item.author_managed?'managed':'automatic'])}</p>${item.notes?`<p class="foreshadow-prose">${esc(item.notes)}</p>`:''}<button type="button" class="secondary" data-edit="${index}" ${item.id?'':'disabled'}>${esc(t.edit)}</button><details><summary>${esc(t.history)}</summary><pre>${esc(JSON.stringify(item.history||[],null,2))}</pre></details>${item.evidence?`<details><summary>${esc(t.evidence)}</summary><p>${esc(item.evidence)}</p></details>`:''}${item.status==='resolved'?`<p>${esc(t.resolution)}: ${esc(item.resolved_chapter??'—')}</p><p>${esc(item.resolution_note||'')}</p>`:''}</article>`).join('')||`<p>${esc(t.empty)}</p>`}</div>
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
  if(typeof module!=='undefined') module.exports={renderBoard,boardURL,editValues,formatTags,parseTags,focusFilters,reportURL,renderReportLinks,batchValues,renderBatchPreview};
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
    dialog.querySelector('.utility-dialog-body').innerHTML=`<p>${esc(t.note)}</p><div class="foreshadow-filters">${input('query',t.search,'','text','maxlength="200"')}${select('status',t.status,['all','open','resolved','cancelled'],'all',t)}${select('due',t.due,['all','overdue','due_soon','scheduled','unplanned','closed'],'all',t)}${select('priority',t.priority,['all','high','normal','low'],'all',t)}${select('ownership',t.ownership,['all','author','summary'],'all',t)}${input('tag',t.tag,'','text','maxlength="40"')}${input('current_chapter',t.clock,'','number','min="0" max="1000000" step="1"')}${input('due_within',t.window,5,'number','required min="0" max="1000000" step="1"')}</div><div class="foreshadow-actions"><button type="button" class="primary" id="filterForeshadows">${esc(t.apply)}</button><button type="button" class="secondary" id="createForeshadow">${esc(t.create)}</button><button type="button" class="secondary" id="copyForeshadows" disabled>${esc(t.export)}</button></div><div class="foreshadow-actions">${select('focus',t.focus,['all','overdue','upcoming','urgent','mine'],'all',t)}<button type="button" id="batchForeshadows" disabled>${esc(t.batch)}</button><span id="foreshadowSelectionCount" aria-live="polite"></span></div><details><summary>${esc(t.download)}</summary><p>${esc(t.exportNote)}</p><label class="foreshadow-select"><input type="checkbox" id="foreshadowReportNotes"> ${esc(t.notesOpt)}</label><div class="foreshadow-actions">${renderReportLinks(novel)}</div></details><label class="foreshadow-select"><input type="checkbox" id="foreshadowSelectPage" disabled> ${esc(t.selectPage)}</label><div id="foreshadowBoardResults" aria-live="polite"></div>`;
    dialog.showModal();
    const host=$('foreshadowBoardResults'),filter=$('filterForeshadows'),copy=$('copyForeshadows');
    const active=()=>generation===id && dialog.open && state.novel===novel;
    let sequence=0,editorSequence=0,lastFilters={},board=null,changed=false;
    const selected=new Set(),batch=$('batchForeshadows'),selectPage=$('foreshadowSelectPage');
    const updateSelection=()=>{
      batch.disabled=!selected.size;
      $('foreshadowSelectionCount').textContent=selected.size+' '+t.selected;
      selectPage.checked=Boolean(board?.items.length)&&selected.size===board.items.length;
      selectPage.indeterminate=selected.size>0&&!selectPage.checked;
    };
    const reportButtons=dialog.querySelectorAll('[data-report]');
    const reportReady=ready=>reportButtons.forEach(link=>{
      link.setAttribute('aria-disabled',String(!ready));link.tabIndex=ready?0:-1;
      if(ready&&board)link.href=reportURL(novel,{...lastFilters,current_chapter:board.current_chapter},link.dataset.report,$('foreshadowReportNotes').checked);
    });
    $('foreshadowReportNotes').onchange=()=>reportReady(Boolean(board)&&!filter.disabled);
    selectPage.onchange=()=>{
      selected.clear();
      host.querySelectorAll('[data-select]').forEach(box=>{box.checked=selectPage.checked;if(box.checked)selected.add(Number(box.dataset.select));});
      updateSelection();
    };
    dialog.querySelector('[name="focus"]').onchange=event=>{
      for(const [key,value] of Object.entries(focusFilters(event.target.value)))dialog.querySelector('.foreshadow-filters [name="'+key+'"]').value=value;
      refresh();
    };
    for(const button of reportButtons)button.onclick=event=>{
      if(!active()||!board||button.getAttribute('aria-disabled')==='true'){event.preventDefault();return;}
      const filters={...lastFilters,current_chapter:board.current_chapter};
      button.href=reportURL(novel,filters,button.dataset.report,$('foreshadowReportNotes').checked);
    };
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
      const ticket=++sequence;selected.clear();updateSelection();selectPage.disabled=true;
      reportReady(false);
      filter.disabled=true;copy.disabled=true;host.textContent=t.loading;
      try {
        const result=await json(boardURL(novel,{...lastFilters,offset,limit:20}));
        if(!active()||sequence!==ticket)return;
        board=result.board;host.innerHTML=renderBoard(board,lang);
        selectPage.disabled=!board.items.length;
        reportReady(true);
        host.querySelectorAll('[data-select]').forEach(box=>box.onchange=()=>{
          const index=Number(box.dataset.select);if(box.checked)selected.add(index);else selected.delete(index);
          updateSelection();
        });
        host.querySelectorAll('[data-offset]').forEach(button=>button.onclick=()=>refresh(Number(button.dataset.offset),false));
        host.querySelectorAll('[data-edit]').forEach(button=>button.onclick=()=>edit(board.items[Number(button.dataset.edit)]));
        copy.disabled=false;
      }catch(error){if(active()&&sequence===ticket)host.textContent=error.message;}
      finally {if(active()&&sequence===ticket)filter.disabled=false;}
    }
    async function batchEdit() {
      if(!active()||!board||!selected.size)return;
      const selection=[...selected].sort((a,b)=>a-b).map(index=>({id:board.items[index].id,expected_revision:board.items[index].revision}));
      const editTicket=++editorSequence,editor=ensureUtilityDialog('foreshadowBatchDialog',t.batch);
      editor.querySelector('h2').textContent=t.batch;
      editor.querySelector('.dialog-x').formNoValidate=true;
      editor.querySelector('.utility-dialog-body').innerHTML=`<p>${esc(t.batchNote)}</p><p>${selection.length} ${esc(t.selected)}</p><div class="foreshadow-filters">${select('target_mode',t.targetMode,['keep','shift','set'],'keep',t)}${input('target_value',t.targetValueBatch,'','number','min="-1000000" max="1000001" step="1"')}${select('priority',t.priority,['keep','high','normal','low'],'keep',t)}${select('status',t.status,['keep','open','resolved','cancelled'],'keep',t)}${input('add_tags',t.addTags,'')}${input('remove_tags',t.removeTags,'')}${select('resolution_mode',t.resolutionMode,['keep','setResolution','clearResolution'],'keep',t)}${input('resolved_chapter',t.resolution,'','number','min="1" max="1000001" step="1"')}${input('resolution_note',t.resolutionNote,'','text','maxlength="2000"')}</div><div class="foreshadow-actions"><button type="button" id="previewForeshadowBatch">${esc(t.preview)}</button><button type="button" class="primary" id="applyForeshadowBatch" disabled>${esc(t.commit)}</button></div><p id="foreshadowBatchError" role="alert"></p><div id="foreshadowBatchPreview" aria-live="polite"></div>`;
      editor.showModal();
      const preview=$('previewForeshadowBatch'),apply=$('applyForeshadowBatch'),error=$('foreshadowBatchError'),output=$('foreshadowBatchPreview');
      let reviewed=null;
      const live=()=>active()&&editor.open&&editorSequence===editTicket;
      const controls=[...editor.querySelectorAll('[name]')];
      const busy=value=>{preview.disabled=value;apply.disabled=value||!reviewed;controls.forEach(control=>control.disabled=value);};
      for(const control of controls)control.oninput=()=>{reviewed=null;apply.disabled=true;output.textContent='';};
      preview.onclick=async()=>{
        if(!live())return;
        error.textContent='';reviewed=null;output.textContent='';
        try{
          const form={};for(const control of controls){if(!control.reportValidity())return;form[control.name]=control.value;}
          const payload={selection,changes:batchValues(form)};
          busy(true);
          const response=await json(`/api/novels/${enc(novel)}/foreshadow-batch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,dry_run:true})});
          if(!live())return;
          output.innerHTML=renderBatchPreview(response.result,lang);
          if(response.result.changed)reviewed=payload;
        }catch(e){if(live())error.textContent=e.message;}
        finally{if(live())busy(false);}
      };
      apply.onclick=async()=>{
        if(!live()||!reviewed)return;
        busy(true);error.textContent='';
        try{
          await json(`/api/novels/${enc(novel)}/foreshadow-batch`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...reviewed,dry_run:false})});
          changed=true;reviewed=null;
          if(live())editor.close();
          if(active())await refresh(0,false);
        }catch(e){reviewed=null;if(live())error.textContent=e.message;}
        finally{if(live())busy(false);}
      };
      editor.querySelector('form').onsubmit=event=>{if(event.submitter?.classList.contains('dialog-x'))return;event.preventDefault();if(!preview.disabled)preview.click();};
    }
    batch.onclick=batchEdit;
    async function edit(item=null) {
      const editTicket=++editorSequence;
      const editor=ensureUtilityDialog('foreshadowEditorDialog',item?t.edit:t.create),old=item||{};
      editor.querySelector('h2').textContent=item?t.edit:t.create;
      editor.querySelector('.dialog-x').formNoValidate=true;
      const area=(name,label,value,max)=>`<label>${esc(label)}<textarea name="${name}" maxlength="${max}" ${name==='text'?'required':''}>${esc(value)}</textarea></label>`;
      editor.querySelector('.utility-dialog-body').innerHTML=`<p>${esc(t.note)}</p>${area('text',t.text,old.text||'',2000)}<div class="foreshadow-filters">${item?'':input('introduced_chapter',t.introduced,0,'number','required min="0" max="1000000" step="1"')}${input('target_chapter',t.target,old.target_chapter??Math.min(1000001,(board?.current_chapter||0)+10),'number','required min="1" max="1000001" step="1"')}${select('priority',t.priority,['high','normal','low'],old.priority||'normal',t)}${item?select('status',t.status,['open','resolved','cancelled'],old.status,t):''}${input('tags',t.tags,formatTags(old.tags||[]))}</div>${area('notes',t.notes,old.notes||'',4000)}${item?`<div id="foreshadowResolutionFields">${input('resolved_chapter',t.resolution,old.resolved_chapter??'','number','min="1" max="1000001" step="1"')}${area('resolution_note',t.resolutionNote,old.resolution_note||'',2000)}</div>`:''}<p>${esc(t.conflict)}</p><button type="button" class="primary" id="saveForeshadowRecord">${esc(t.save)}</button><p id="foreshadowEditorError" role="alert"></p>`;
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
