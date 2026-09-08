/* A read-only, directed relationship inspector. No author data is rewritten. */
(() => {
  const words = {
    en: {title:'Character relationships', focus:'Focal character', role:'Role', all:'All roles', chapter:'As of chapter (blank = latest)', refresh:'Refresh relationships', empty:'No matching relationships.', history:'Evidence history', profile:'Undated profile', ledger:'Chapter observation', more:'Show more', note:'Chapter filters exclude undated profile prose. Role, status and appearance ranges use current profiles. Observations do not replace author review.', unresolved:'Unresolved profile notes', graphLimit:'Large network: use the character or role filters to display the graph.', visible:'Displayed relationships', evidence:'No evidence recorded', unregistered:'Unregistered', roles:['Protagonist','Major supporting','Minor supporting','NPC','Background']},
    zh: {title:'人物关系图谱', focus:'焦点人物', role:'角色层级', all:'全部层级', chapter:'截至章节（留空为最新）', refresh:'刷新关系', empty:'没有符合条件的关系。', history:'证据历史', profile:'未标注章节的档案', ledger:'章节观察记录', more:'显示更多', note:'章节筛选不包含未标注章节的档案描述。层级、状态及出场范围使用当前档案。观察记录不替代作者审核。', unresolved:'未解析的档案描述', graphLimit:'关系网络较大，请筛选人物或层级以显示图形。', visible:'已显示关系', evidence:'未记录证据', unregistered:'未建档', roles:['主角','重要配角','次要角色','NPC','路人']},
    ja: {title:'人物関係図', focus:'中心人物', role:'役割', all:'すべての役割', chapter:'対象章まで（空欄は最新）', refresh:'関係を更新', empty:'該当する関係がありません。', history:'根拠の履歴', profile:'章未指定のプロフィール', ledger:'章の観察記録', more:'さらに表示', note:'章の絞り込みでは章未指定の説明を除外します。役割・状態・登場範囲は現在のプロフィールです。観察記録は作者の確認を代替しません。', unresolved:'未解析のプロフィール説明', graphLimit:'大きな関係図です。人物や役割で絞り込んでください。', visible:'表示中の関係', evidence:'根拠の記録なし', unregistered:'未登録', roles:['主人公','主要脇役','脇役','NPC','背景人物']}
  };
  const roles = ['主角','重要配角','次要角色','NPC','路人'];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const key = edge => JSON.stringify([edge.from, edge.to]);
  function renderNetwork(network, language = 'en', limit = 100) {
    const t = words[language] || words.en;
    const nodes = network.nodes || [], edges = network.edges || [];
    const history = new Map();
    for (const item of network.history || []) {
      const id = key(item);
      if (!history.has(id)) history.set(id, []);
      history.get(id).push(item);
    }
    let graph = '';
    if (nodes.length > 16) graph = `<p>${escape(t.graphLimit)}</p>`;
    else if (nodes.length) {
      const positions = new Map(nodes.map((node, index) => [node.id, {x:380+135*Math.cos(index/nodes.length*2*Math.PI), y:180+135*Math.sin(index/nodes.length*2*Math.PI)}]));
      const paths = edges.map(edge => {
        const a = positions.get(edge.from), b = positions.get(edge.to);
        if (!a || !b || edge.from === edge.to) return '';
        const dx=b.x-a.x, dy=b.y-a.y, length=Math.hypot(dx,dy), ux=dx/length, uy=dy/length;
        const color=edge.strength<0?'#dc6573':edge.strength>0?'#42a88b':'#8d94ad';
        return `<path d="M ${a.x+ux*25} ${a.y+uy*25} Q ${(a.x+b.x)/2-uy*22} ${(a.y+b.y)/2+ux*22} ${b.x-ux*28} ${b.y-uy*28}" fill="none" stroke="${color}" stroke-width="2" marker-end="url(#relationshipArrow)"><title>${escape(edge.from)} → ${escape(edge.to)}: ${escape(edge.type)}</title></path>`;
      }).join('');
      graph=`<svg viewBox="0 0 760 360" class="relation-svg" role="img" aria-label="${escape(t.title)}"><defs><marker id="relationshipArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8d94ad"/></marker></defs>${paths}${nodes.map(node=>{const p=positions.get(node.id);return `<g class="relation-node"><title>${escape(node.id)} · ${escape(node.role_tier)}${node.registered?'':` · ${escape(t.unregistered)}`}</title><circle cx="${p.x}" cy="${p.y}" r="24"/><text x="${p.x}" y="${p.y+4}">${escape(node.id)}</text></g>`;}).join('')}</svg>`;
    }
    const rows = edges.slice(0,limit).map(edge => {
      const events = history.get(key(edge)) || [];
      return `<details class="list-card"><summary>${escape(edge.from)} → ${escape(edge.to)} · ${escape(edge.type)} · ${escape(edge.strength ?? '—')} · ${escape(edge.source === 'profile' ? t.profile : `${t.ledger} #${edge.chapter}`)}</summary><p>${escape(edge.evidence || t.evidence)}</p>${events.length?`<h4>${escape(t.history)}</h4><ol>${events.map(item=>`<li>#${escape(item.chapter)} · ${escape(item.type)} · ${escape(item.strength)}<p>${escape(item.evidence || t.evidence)}</p></li>`).join('')}</ol>`:''}</details>`;
    }).join('');
    const notes=(network.profile_notes||[]).map(item=>`<li>${escape(item.character)}: ${escape(item.text)}</li>`).join('');
    return `${graph}<p>${escape(t.visible)}: ${Math.min(edges.length,limit)} / ${edges.length}</p>${rows||`<p>${escape(t.empty)}</p>`}${edges.length>limit?`<button type="button" class="secondary" id="moreRelationships">${escape(t.more)}</button>`:''}${notes?`<details><summary>${escape(t.unresolved)}</summary><ul>${notes}</ul></details>`:''}`;
  }
  if (typeof module !== 'undefined') module.exports = {renderNetwork};
  if (typeof document === 'undefined') return;
  let generation = 0;
  async function load() {
    const id = ++generation, novel = state.novel;
    if (!novel) return;
    const language = localStorage.getItem('novel-ui-language') || 'en', t = words[language] || words.en;
    let box = $('relationshipGraph');
    const same = box?.dataset.novel === novel;
    const filters = {character:same?$('relationshipFocus')?.value||'':'', role_tier:same?$('relationshipRole')?.value||'':'', chapter:same?$('relationshipChapter')?.value||'':''};
    if (!box) {box=document.createElement('section');box.id='relationshipGraph';$('panel-characters').append(box);}
    box.dataset.novel=novel;
    box.innerHTML=`<h3>${escape(t.title)}</h3><p class="hint">${escape(t.note)}</p><form id="relationshipFilters" class="form-grid"><label>${escape(t.focus)}<input id="relationshipFocus" list="relationshipNames" value="${escape(filters.character)}"><datalist id="relationshipNames">${(state.characters||[]).map(item=>`<option value="${escape(item.name)}"></option>`).join('')}</datalist></label><label>${escape(t.role)}<select id="relationshipRole"><option value="">${escape(t.all)}</option>${roles.map((role,i)=>`<option value="${role}"${filters.role_tier===role?' selected':''}>${t.roles[i]}</option>`).join('')}</select></label><label>${escape(t.chapter)}<input id="relationshipChapter" type="number" min="0" step="1" value="${escape(filters.chapter)}"></label><button class="secondary">${escape(t.refresh)}</button></form><div id="relationshipResults" aria-live="polite"></div>`;
    $('relationshipFilters').onsubmit=event=>{event.preventDefault();load().catch(error=>toast(error.message,true));};
    const host=$('relationshipResults');
    const params=new URLSearchParams(Object.entries(filters).filter(([,value])=>value!==''));
    try {
      const data=await json(`/api/novels/${enc(novel)}/character-network?${params}`);
      if (id!==generation || state.novel!==novel || $('relationshipResults')!==host) return;
      let limit=100;
      function render() {
        host.innerHTML=renderNetwork(data.network,language,limit);
        host.querySelector('#moreRelationships')?.addEventListener('click',()=>{limit+=100;render();});
      }
      render();
    } catch(error) {
      if (id===generation && state.novel===novel && $('relationshipResults')===host) host.textContent=error.message;
    }
  }
  window.NovelRelationships={load};
  document.addEventListener('DOMContentLoaded',()=>{
    $('languageSelect')?.addEventListener('change',()=>{
      if ($('panel-characters')?.classList.contains('active')) load().catch(error=>toast(error.message,true));
    });
  });
})();
