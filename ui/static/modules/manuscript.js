/* Read-only diagnostics: all manuscript-derived text is escaped before rendering. */
(() => {
  const labels = {
    en: {title:'Manuscript diagnostics',note:'Read-only, no AI calls. Units count Han/Kana characters and letter/digit words, not stored word counts. Repeats compare nonempty lines with normalized whitespace. Reports are heuristics, not quality scores or atomic snapshots.',start:'First chapter',end:'Last chapter (optional)',target:'Target units (0 = off)',speed:'Reading units / minute',minimum:'Minimum repeated characters',excerpts:'Include private text excerpts',run:'Analyze manuscript',complete:'Scan completed',partial:'Partial scan — inspect skipped files',chapters:'Chapters scanned',units:'Total units',reading:'Reading minutes (estimate)',missing:'Missing chapters',empty:'Empty chapters',duplicates:'Repeated paragraph groups',table:'Chapter lengths',chapter:'Chapter',delta:'Change',progress:'Target %',issues:'Findings',none:'No findings in this section.',previous:'Previous',next:'Next',page:'Page',loading:'Analyzing…',export:'Download report (fresh scan)',truncated:'Report limits omit additional groups or locations.',issueLimit:'Showing the first 100 findings; export the report for all findings.',location:'Chapter / line',excerptWarning:'Excerpts may contain private manuscript text. Review before sharing.'},
    zh: {title:'全书诊断',note:'只读检查，不调用模型。统计单位为汉字、假名及字母/数字词组，不修改项目字数。重复检测按非空行归一化空白后精确匹配。结果为启发式提示，不是质量评分或原子快照。',start:'起始章节',end:'结束章节（选填）',target:'目标篇幅（0 关闭）',speed:'每分钟阅读单位',minimum:'重复段落最少字符',excerpts:'包含私密原文摘录',run:'分析全书',complete:'扫描完成',partial:'部分扫描，请查看跳过的文件',chapters:'已扫描章节',units:'总篇幅单位',reading:'预计阅读分钟',missing:'缺失章节',empty:'空白章节',duplicates:'跨章重复段落组',table:'章节篇幅',chapter:'章节',delta:'篇幅变化',progress:'目标 %',issues:'检查结果',none:'此部分暂无发现。',previous:'上一页',next:'下一页',page:'页',loading:'分析中…',export:'下载报告（重新扫描）',truncated:'报告上限省略了部分分组或位置。',issueLimit:'仅显示前 100 条发现；导出报告查看全部。',location:'章节 / 行号',excerptWarning:'摘录可能含有私密正文，分享前请检查。'},
    ja: {title:'原稿診断',note:'読み取り専用で、AI は呼び出しません。単位は漢字・仮名と文字/数字の単語で、保存済み文字数は変更しません。空白を正規化した非空行を完全一致で比較します。品質評価や原子的スナップショットではありません。',start:'開始章',end:'終了章（任意）',target:'目標単位（0 で無効）',speed:'1 分あたりの読書単位',minimum:'重複行の最小文字数',excerpts:'非公開本文の抜粋を含める',run:'原稿を分析',complete:'スキャン完了',partial:'部分スキャン — スキップしたファイルを確認',chapters:'分析した章',units:'合計単位',reading:'推定読書時間（分）',missing:'欠落した章',empty:'空の章',duplicates:'章間の重複段落',table:'章の長さ',chapter:'章',delta:'増減',progress:'目標 %',issues:'検出結果',none:'この項目に検出結果はありません。',previous:'前へ',next:'次へ',page:'ページ',loading:'分析中…',export:'レポートをダウンロード（再スキャン）',truncated:'上限により一部のグループや位置が省略されています。',issueLimit:'最初の 100 件を表示しています。全件はレポートを出力してください。',location:'章 / 行',excerptWarning:'抜粋には非公開の本文が含まれる場合があります。共有前に確認してください。'}
  };
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function reportURL(novel, settings, format='view') {
    const query=new URLSearchParams({format});
    for(const [key,value] of Object.entries(settings)) if(value!==null && value!==undefined && value!=='') query.set(key,String(value));
    return `/api/novels/${encodeURIComponent(novel)}/manuscript-report?${query}`;
  }
  function renderReport(report, language='en', page=0) {
    const t=labels[language]||labels.en, s=report.summary, pages=Math.max(1,Math.ceil(report.chapters.length/50));
    page=Math.max(0,Math.min(pages-1,Math.trunc(Number(page))||0));
    const rows=report.chapters.slice(page*50,(page+1)*50), peak=Math.max(1,...rows.map(c=>c.units));
    const cards=[['chapters_scanned',t.chapters],['total_units',t.units],['reading_minutes',t.reading],['missing_count',t.missing],['empty_count',t.empty],['duplicate_groups',t.duplicates]];
    const trend=rows.map((c,index)=>`<rect x="${index*12}" y="${100-c.units/peak*95}" width="9" height="${c.units/peak*95}"><title>${escape(t.chapter)} ${escape(c.chapter)}: ${escape(c.units)}</title></rect>`).join('');
    return `<p class="manuscript-status ${report.complete?'':'partial'}">${escape(report.complete?t.complete:t.partial)}</p><div class="manuscript-cards">${cards.map(([key,label])=>`<div><b>${escape(s[key])}</b><span>${escape(label)}</span></div>`).join('')}</div>
      <h3>${escape(t.table)}</h3><svg class="manuscript-trend" role="img" aria-label="${escape(t.table)}" viewBox="0 0 600 105"><title>${escape(t.table)} — ${escape(t.page)} ${page+1}</title>${trend}</svg>
      <div class="manuscript-table"><table><thead><tr>${[t.chapter,t.units,t.delta,t.reading,t.progress].map(v=>`<th>${escape(v)}</th>`).join('')}</tr></thead><tbody>${rows.map(c=>`<tr><td>${escape(c.chapter)}</td><td>${escape(c.units)}</td><td>${escape(c.delta_units??'—')}</td><td>${escape(c.reading_minutes)}</td><td>${escape(c.target_percent??'—')}</td></tr>`).join('')}</tbody></table></div>
      <div class="manuscript-pagination"><button type="button" data-page="${page-1}" ${page===0?'disabled':''}>${escape(t.previous)}</button><span>${escape(t.page)} ${page+1} / ${pages}</span><button type="button" data-page="${page+1}" ${page===pages-1?'disabled':''}>${escape(t.next)}</button></div>
      <h3>${escape(t.issues)}</h3><ul>${report.issues.slice(0,100).map(i=>`<li><b>${escape(i.code)}</b>: ${escape(i.message)} <small>${escape(JSON.stringify(Object.fromEntries(Object.entries(i).filter(([k])=>!['code','message'].includes(k)))))}</small></li>`).join('')||`<li>${escape(t.none)}</li>`}</ul>${report.issues.length>100?`<p>${escape(t.issueLimit)}</p>`:''}
      <h3>${escape(t.duplicates)}</h3>${report.duplicates.map(d=>`<details><summary>${escape(d.occurrence_count)} × · ${escape(d.chapter_count)} chapters · ${escape(d.characters)} chars</summary><p>${escape(t.location)}: ${d.locations.map(l=>`${escape(l.chapter)} / ${escape(l.line)}`).join(', ')}</p>${d.excerpt?`<blockquote>${escape(d.excerpt)}</blockquote>`:''}<code>${escape(d.fingerprint)}</code>${d.locations_truncated?`<p>${escape(t.truncated)}</p>`:''}</details>`).join('')||`<p>${escape(t.none)}</p>`}${s.duplicates_truncated?`<p>${escape(t.truncated)}</p>`:''}`;
  }
  if(typeof module!=='undefined') module.exports={renderReport,reportURL};
  if(typeof document==='undefined') return;
  let sequence=0;
  async function openManuscript() {
    const novel=state.novel;
    if(!novel) return;
    const session=++sequence, language=localStorage.getItem('novel-ui-language')||'en', t=labels[language]||labels.en;
    const dialog=ensureUtilityDialog('manuscriptDialog',t.title);
    dialog.querySelector('h2').textContent=t.title;
    const input=(key,label,value,min,max)=>`<label>${escape(label)}<input name="${key}" type="number" step="1" min="${min}" max="${max}" value="${value}" ${key==='end_chapter'?'':'required'}></label>`;
    dialog.querySelector('.utility-dialog-body').innerHTML=`<p class="hint">${escape(t.note)}</p><div class="manuscript-filters">${input('start_chapter',t.start,1,1,1000000)}${input('end_chapter',t.end,'',1,1000000)}${input('target_units',t.target,0,0,1000000)}${input('units_per_minute',t.speed,300,1,10000)}${input('min_repeat_chars',t.minimum,40,10,1000)}</div><label class="manuscript-check"><input type="checkbox" name="include_excerpts">${escape(t.excerpts)}</label><p class="hint">${escape(t.excerptWarning)}</p><button type="button" class="primary" id="runManuscript">${escape(t.run)}</button><div id="manuscriptExports"></div><div id="manuscriptResults" aria-live="polite"></div>`;
    dialog.showModal();
    const host=$('manuscriptResults'), exports=$('manuscriptExports'), run=$('runManuscript');
    const active=()=>sequence===session && dialog.open && state.novel===novel;
    let request=0;
    async function refresh() {
      const settings={};
      for(const control of dialog.querySelectorAll('.manuscript-filters input')) {
        if(!control.reportValidity()) return;
        settings[control.name]=control.value===''?null:Number(control.value);
      }
      settings.include_excerpts=dialog.querySelector('[name="include_excerpts"]').checked;
      const current=++request;
      run.disabled=true;host.textContent=t.loading;exports.replaceChildren();
      try {
        const data=await json(reportURL(novel,settings));
        if(!active()||current!==request) return;
        const draw=page=>{
          host.innerHTML=renderReport(data.report,language,page);
          host.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>draw(Number(button.dataset.page)));
        };
        draw(0);
        exports.innerHTML=`<p>${escape(t.export)}: <a download href="${escape(reportURL(novel,settings,'json'))}">JSON</a> · <a download href="${escape(reportURL(novel,settings,'markdown'))}">Markdown</a></p>`;
      } catch(error) {if(active()&&current===request) host.textContent=error.message;}
      finally {if(active()&&current===request) run.disabled=false;}
    }
    run.onclick=refresh;
    // The utility dialog uses method=dialog. Enter in a filter must analyze, not close it.
    dialog.querySelector('.manuscript-filters').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();if(!run.disabled) refresh();}};
    await refresh();
  }
  document.addEventListener('DOMContentLoaded',()=>{
    const anchor=$('refreshDashboard');
    if(!anchor) return;
    const button=document.createElement('button');
    button.id='manuscriptDiagnosticsButton';button.type='button';button.className='secondary';
    const update=()=>{button.textContent=(labels[localStorage.getItem('novel-ui-language')||'en']||labels.en).title;};
    update();button.onclick=openManuscript;anchor.before(button);
    $('languageSelect')?.addEventListener('change',update);
  });
})();
