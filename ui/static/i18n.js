/* English-first UI localization. The selected language is kept only in this browser. */
(function () {
  const dict = {
    en: {
      "墨境": "MoJing", "创建小说": "Create novel", "＋ 创建小说": "+ Create novel", "导入小说 / 项目": "Import novel / project", "TXT 或完整项目ZIP": "TXT or full project ZIP",
      "回收站": "Trash", "恢复误删作品": "Restore deleted works", "设置中心": "Settings", "自动化与硬件安全策略": "Automation & hardware safety", "作品": "Novels", "本地模型": "Local model",
      "从一个想法，到一部长篇小说": "From one idea to a complete novel", "设定、人物、章节、时间线都在一个页面完成。": "World-building, characters, chapters and timelines in one workspace.", "开始第一部小说": "Start your first novel",
      "仪表盘": "Dashboard", "创作台": "Writing studio", "故事设定": "Story bible", "人物管理": "Characters", "章节管理": "Chapters", "时间线": "Timeline", "作品仪表盘": "Novel dashboard", "刷新": "Refresh",
      "集中查看进度、风险和后台任务。": "Monitor progress, risks and background tasks in one place.", "统一审核队列": "Unified review queue", "最近任务": "Recent tasks", "AI 运行现场": "AI runtime", "停止任务": "Stop task", "收起": "Collapse", "展开": "Expand",
      "本章导演台": "Chapter director", "下一章目标": "Next chapter goal", "章前提要": "Chapter brief", "生成字数": "Target words", "高级参数": "Advanced parameters", "创意度": "Creativity",
      "AI 生成下一章": "Generate next chapter", "根据现有内容续写": "Continue from current content", "后台连续生成多章": "Generate multiple chapters", "清空": "Clear", "保存章节": "Save chapter", "等待创作": "Ready to write",
      "内容仅保存在本机": "Content stays on this machine", "故事设定与层级规划": "Story bible & layered planning", "保存设定": "Save story bible", "AI 分阶段重策划": "Re-plan with AI",
      "人物档案": "Character profiles", "新建人物": "New character", "待确认人物变化": "Pending character changes", "导出": "Export", "历史版本": "History", "检查一致性": "Check continuity", "关键事件": "Key events", "伏笔生命周期": "Foreshadowing lifecycle",
      "取消": "Cancel", "下一步": "Next", "上一步": "Back", "确认并创建小说": "Create novel", "英文": "English", "中文": "Chinese", "日本語": "Japanese", "本地模型管理": "Local model manager", "已连接": "Connected", "未连接": "Disconnected",
      "重新连接并预热": "Reconnect & warm up", "暂停": "Pause", "继续": "Resume", "保存设置": "Save settings", "导入小说": "Import novel", "小说回收站": "Novel trash", "章节历史版本": "Chapter history", "局部AI编辑对比": "AI edit comparison", "保留原文": "Keep original", "采用修改稿": "Apply revision", "等待生成任务": "Waiting for a task", "暂无任务记录": "No task history", "当前没有待审核事项。": "No review items are waiting", "尚未设置权威锁。": "No canonical locks configured", "尚未设置地点移动耗时。": "No travel-time rules configured"
    },
    zh: {},
    ja: {"墨境":"MoJing","创建小说":"小説を作成","导入小说 / 项目":"小説 / プロジェクトをインポート","回收站":"ゴミ箱","设置中心":"設定","作品":"作品","本地模型":"ローカルモデル","仪表盘":"ダッシュボード","创作台":"執筆スタジオ","故事设定":"ストーリーバイブル","人物管理":"登場人物","章节管理":"章管理","时间线":"タイムライン","开始第一部小说":"最初の小説を始める","从一个想法，到一部长篇小说":"ひとつのアイデアから長編小説へ","刷新":"更新","保存章节":"章を保存","保存设定":"設定を保存","导出":"エクスポート","检查一致性":"整合性を確認","取消":"キャンセル","下一步":"次へ","上一步":"戻る","英文":"English","中文":"中文","日本語":"日本語"}
  };
  const titles = { en: "MoJing · AI Novel Studio", zh: "墨境 · AI 小说工作台", ja: "MoJing · AI 小説スタジオ" };
  const current = () => localStorage.getItem("novel-ui-language") || "en";
  function text(node, target) {
    const source = node.__i18nSource || node.nodeValue;
    node.__i18nSource = source;
    const key = source.trim();
    const value = target === "zh" ? key : ((dict[target] || {})[key] || key);
    node.nodeValue = key ? source.replace(key, value) : source;
  }
  function apply(root, target) {
    const walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT), nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode); nodes.forEach(node => text(node, target));
    (root || document).querySelectorAll?.("[placeholder],[title]").forEach(el => ["placeholder", "title"].forEach(attr => { const source = el.dataset[`i18n${attr}`] || el.getAttribute(attr); if (source) { el.dataset[`i18n${attr}`] = source; el.setAttribute(attr, (dict[target] || {})[source] || source); } }));
    document.documentElement.lang = target === "ja" ? "ja" : target === "zh" ? "zh-CN" : "en";
    document.title = titles[target] || titles.en;
  }
  function setLanguage(target) { localStorage.setItem("novel-ui-language", target); apply(document.body, target); const select = document.getElementById("languageSelect"); if (select) select.value = target; }
  window.NovelI18n = { setLanguage, apply };
  document.addEventListener("DOMContentLoaded", () => { const select = document.getElementById("languageSelect"); if (select) { select.value = current(); select.addEventListener("change", () => setLanguage(select.value)); } apply(document.body, current()); new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => { if (node.nodeType === Node.ELEMENT_NODE) apply(node, current()); else if (node.nodeType === Node.TEXT_NODE) text(node, current()); }))).observe(document.body, {childList:true, subtree:true}); });
})();
