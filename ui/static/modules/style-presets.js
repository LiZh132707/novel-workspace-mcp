/* Presets are staged in the editor; only Save story bible persists them. */
(() => {
  function mergeStyle(current, preset, mode) {
    if (mode === 'replace') return preset;
    if (mode !== 'append') throw new Error('Unknown style merge mode');
    return current.trim() ? `${current.trimEnd()}\n\n${preset}` : preset;
  }
  if (typeof module !== 'undefined') module.exports = { mergeStyle };
  if (typeof document === 'undefined') return;

  async function openStylePresets() {
    const novel = state.novel;
    if (!novel) return;
    const dialog = ensureUtilityDialog('stylePresetsDialog', '风格预设');
    dialog.querySelector('.utility-dialog-body').innerHTML = `
      <p class="hint">选择预设并预览；应用仅修改编辑框，点击保存设定后才会保存。</p>
      <label>选择预设<select id="stylePresetSelect" disabled></select></label>
      <label>预览<textarea id="stylePresetPreview" rows="14" readonly></textarea></label>
      <div class="dialog-actions">
        <button type="button" class="secondary" id="appendStylePreset" disabled>追加到文风</button>
        <button type="button" class="primary" id="replaceStylePreset" disabled>替换文风</button>
      </div>`;
    dialog.showModal();
    const select = $('stylePresetSelect'), preview = $('stylePresetPreview');
    const append = $('appendStylePreset'), replace = $('replaceStylePreset');
    let requestId = 0;
    let styleText = null;
    const active = () => dialog.open && state.novel === novel && $('stylePresetSelect') === select;
    async function loadPreview() {
      const id = ++requestId;
      styleText = null;
      preview.value = '';
      append.disabled = replace.disabled = true;
      if (!select.value) return;
      try {
        const [name, source] = JSON.parse(select.value);
        const params = new URLSearchParams({ preset: name, source });
        const data = await json(`/api/novels/${enc(novel)}/style-presets/preview?${params}`);
        if (!active() || id !== requestId) return;
        styleText = data.style_text;
        preview.value = styleText;
        append.disabled = replace.disabled = false;
      } catch (error) {
        if (active() && id === requestId) toast(error.message, true);
      }
    }
    function apply(mode) {
      if (!active() || styleText === null) return;
      if (mode === 'replace' && $('style').value.trim() && !confirm(
        ({en: 'Replace the current style editor content?', zh: '替换当前文风编辑内容？', ja: '現在の文体の編集内容を置き換えますか？'})[localStorage.getItem('novel-ui-language') || 'en']
      )) return;
      $('style').value = mergeStyle($('style').value, styleText, mode);
      $('style').dispatchEvent(new Event('input', { bubbles: true }));
      dialog.close();
      $('style').focus();
      toast('文风已填入编辑框，请点击保存设定');
    }
    append.onclick = () => apply('append');
    replace.onclick = () => apply('replace');
    select.onchange = loadPreview;
    try {
      const data = await json(`/api/novels/${enc(novel)}/style-presets`);
      if (!active()) return;
      for (const item of data.presets) {
        const option = document.createElement('option');
        const source = item.builtin ? 'builtin' : 'custom';
        option.value = JSON.stringify([item.name, source]);
        option.textContent = `${item.name} [${source}] — ${item.description}`;
        select.append(option);
      }
      select.disabled = false;
      await loadPreview();
    } catch (error) {
      if (active()) toast(error.message, true);
    }
  }
  document.addEventListener('DOMContentLoaded', () => {
    const save = $('saveBible');
    if (!save) return;
    const button = document.createElement('button');
    button.id = 'browseStylePresets';
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = '风格预设';
    button.onclick = openStylePresets;
    save.before(button);
  });
})();
