async function loadCausalGraph() {
  const data = await json(`/api/novels/${enc(state.novel)}/causal-graph`);
  const graph = data.graph || {};
  const stats = graph.stats || {};
  const nodes = Object.fromEntries((graph.nodes || []).map(item => [item.id, item]));
  const panel = $('panel-timeline');
  let box = $('causalGraphBox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'causalGraphBox';
    panel.append(box);
  }
  const planned = (graph.planned_outcomes || []).slice(-30);
  const causes = (graph.edges || []).filter(item => item.type === 'causes').slice(-20);
  box.innerHTML = `<h3>规划—正史因果图</h3><div class="dashboard-grid"><article class="dashboard-stat"><small>规划结果</small><b>${stats.planned || 0}</b></article><article class="dashboard-stat"><small>已有证据 / 迟到</small><b>${stats.evidenced || 0} / ${stats.evidenced_late || 0}</b></article><article class="dashboard-stat"><small>到期缺证</small><b>${stats.due_missing || 0}</b></article><article class="dashboard-stat"><small>因果环</small><b>${stats.cycles || 0}</b></article></div><div class="list-card">${planned.map(item => `<div class="chapter-row"><b>${item.status === 'evidenced' ? '✓' : item.status === 'evidenced_late' ? '△' : item.status === 'future' ? '○' : '⚠'} ${escapeHtml(item.scope || '')} · ${escapeHtml(item.text || '')}</b><small>截止第${item.deadline || '?'}章 · ${item.status === 'evidenced' ? `按期证据第${item.evidence_chapter}章，匹配${Math.round((item.evidence_overlap || 0) * 100)}%` : item.status === 'evidenced_late' ? `迟到证据第${item.evidence_chapter}章` : item.status === 'future' ? '尚未到期' : '已到期但未找到正史证据'}</small></div>`).join('') || '<p class="hint">完成分卷规划并保存章节后会建立目标证据图。</p>'}</div><h4>最近正史因果边</h4><div class="list-card">${causes.map(edge => `<div class="chapter-row"><small>第${edge.chapter || '?'}章 · ${escapeHtml(nodes[edge.source]?.label || '')} → ${escapeHtml(nodes[edge.target]?.label || '')}</small></div>`).join('') || '<p class="hint">摘要中的因果关系会显示在这里。</p>'}</div>`;
}

function initCausalRepairTool() {
  const box = $('causalGraphBox');
  if (!box || $('proposeCausalRepairs')) return;
  const button = document.createElement('button');
  button.id = 'proposeCausalRepairs';
  button.className = 'secondary';
  button.textContent = '把缺证目标转成未来修复提要';
  box.querySelector('h3')?.after(button);
  button.onclick = async () => {
    setBusy(button, true, '生成提案中…');
    try {
      const data = await json(`/api/novels/${enc(state.novel)}/causal-repairs/propose`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({window: 3}),
      });
      const proposal = data.proposal;
      const dialog = ensureUtilityDialog('causalRepairDialog', '未来三章因果修复提案');
      dialog.querySelector('.utility-dialog-body').innerHTML = `<p class="hint">仅生成提案，确认应用前不会修改章节提要。已有标题和提要会被保留，只追加修复约束。</p>${(proposal.patches || []).map(patch => `<article class="review-issue"><strong>第${patch.chapter}章 · ${(patch.gaps || []).map(item => escapeHtml(item.text || '')).join('；')}</strong><p>${escapeHtml(patch.after?.structural_purpose || '')}</p><small>${escapeHtml((patch.after?.must_happen || []).join('；'))}</small>${(patch.invalidations || []).map(item => `<small>△ ${escapeHtml(item)}</small>`).join('')}</article>`).join('')}<button type="button" class="primary" id="applyCausalRepairs">确认应用提案</button>`;
      dialog.showModal();
      $('applyCausalRepairs').onclick = async () => {
        const applyButton = $('applyCausalRepairs');
        setBusy(applyButton, true, '应用中…');
        try {
          await json(`/api/novels/${enc(state.novel)}/causal-repairs/${proposal.id}/apply`, {method: 'POST'});
          dialog.close();
          toast('修复约束已安全合并到未来章节提要');
          await Promise.all([loadCausalGraph(), loadPlanningReviews()]);
          initCausalRepairTool();
        } catch (error) {
          toast(error.message, true);
        } finally {
          setBusy(applyButton, false, '确认应用提案');
        }
      };
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false, '把缺证目标转成未来修复提要');
    }
  };
}

async function loadVolumeReviews() {
  const data = await json(`/api/novels/${enc(state.novel)}/planning-reviews`);
  const items = (data.report?.volume_reviews || []).slice().reverse();
  const panel = $('panel-timeline');
  let box = $('volumeReviewBox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'volumeReviewBox';
    panel.append(box);
  }
  box.innerHTML = `<h3>卷末验收与修复任务</h3><div class="list-card">${items.map(item => `<div class="chapter-row"><div><b>${['likely_complete', 'accepted_after_review'].includes(item.status) ? '✓' : '⚠'} ${escapeHtml(item.volume || '未命名卷')} · ${escapeHtml(item.status || '')}</b><small>第${item.start_chapter}—${item.end_chapter}章 · 必要结果覆盖 ${Math.round((item.required_coverage || 0) * 100)}%</small>${(item.repair_tasks || []).map(task => `<small>${task.status === 'resolved' ? '✓' : task.status === 'deferred' ? '↷' : '○'} ${escapeHtml(task.priority || '')} · ${escapeHtml(task.description || '')} ${task.resolution_mode ? `· ${escapeHtml(task.resolution_mode)}` : ''} ${task.evidence_chapter ? `· 证据第${task.evidence_chapter}章` : ''} ${task.note ? `· ${escapeHtml(task.note)}` : ''} ${task.status !== 'resolved' ? `<button class="text-btn volume-task-action" data-id="${task.id}" data-status="resolved">完成</button><button class="text-btn volume-task-action" data-id="${task.id}" data-status="${task.status === 'deferred' ? 'pending' : 'deferred'}">${task.status === 'deferred' ? '重新激活' : '延期'}</button>` : ''}</small>`).join('')}</div></div>`).join('') || '<p class="hint">到达卷末后会汇总整卷摘要、人物变化、伏笔和叙事承诺进行验收。</p>'}</div>`;
  box.querySelectorAll('.volume-task-action').forEach(button => button.onclick = async () => {
    const status = button.dataset.status;
    const payload = {status};
    if (status === 'resolved') {
      const chapter = Number(prompt('提供完成证据所在章节（留空则走人工豁免）', state.chapter || ''));
      const quote = chapter ? prompt('粘贴该章中的证据原文，系统会核对', '') : '';
      if (chapter && quote) {
        payload.evidence_chapter = chapter;
        payload.evidence_quote = quote;
        payload.note = prompt('补充说明（可留空）', '') || '';
      } else {
        if (!confirm('没有正文证据，确定以人工豁免方式关闭任务？')) return;
        payload.waive = true;
        payload.note = prompt('必须填写豁免理由', '');
        if (!payload.note) return toast('人工豁免必须填写理由', true);
      }
    } else {
      payload.note = prompt(status === 'pending' ? '重新激活说明' : '延期原因与计划处理章节', '');
      if (payload.note === null) return;
    }
    await json(`/api/novels/${enc(state.novel)}/planning-reviews/volume-tasks/${button.dataset.id}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    await loadVolumeReviews();
    toast(status === 'resolved' ? '修复任务已完成' : status === 'pending' ? '修复任务已重新激活' : '修复任务已延期');
  });
}
