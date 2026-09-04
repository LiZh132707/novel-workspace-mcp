# 墨境 · Novel Workspace MCP

[![CI](https://github.com/LiZh132707/novel-workspace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/LiZh132707/novel-workspace-mcp/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/LiZh132707/novel-workspace-mcp?display_name=tag&sort=semver)](https://github.com/LiZh132707/novel-workspace-mcp/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**[English](README.md) · 中文 · [日本語](README.ja.md)**

> 从一个想法，到一部长篇小说。From idea to an entire novel.

> Web 工作台现已升级为单页全自动创作系统。MCP 工具仍保持兼容。

本项目提供三种入口，共用同一套小说状态、工作流和模型适配层：

1. **本地网页工作台**：浏览器中的完整创作控制中心。
2. **MCP Server**：供 LM Studio、Claude Desktop、Codex 等 MCP 客户端调用。
3. **Codex Skill**：`skills/novel-workspace/`，让 Codex 按本项目的长篇创作和一致性工作流工作。

模型后端支持两种模式：默认连接本地 LM Studio，也可以连接任意 OpenAI-compatible API。两种模式都支持文本生成和向量嵌入。

网页工作台默认使用 English 界面，可在左侧 Language 菜单切换中文或日本語；语言偏好只保存在当前浏览器。版本变更记录见 [`CHANGELOG.md`](CHANGELOG.md)，默认使用 English 编写。

## Web 工作台主要能力

- 一句话创建小说，AI 分阶段生成世界观、规则、文风、总纲、首章目标和人物。
- 章节规划 → 正文生成 → 质量修订 → 摘要 → 人物建议 → 事实/伏笔 → 一致性检查流水线。
- 保存章节时在同一次摘要调用内提取连续性交接与计划对账；下一章优先读取经过正文指纹和逐字证据校验的结尾现场、未闭环与新增约束。
- 生成现场显示上下文总量、健康度、主要记忆来源 Token 占用以及是否装载上一章交接。
- 主页面“创作控制中心”集中提供可恢复工作流、场景细纲、Prompt 最终预览、交接审核、动态状态卡、题材方法包、规划影响、剧情沙盒和长篇评测。
- 统一 AI 动作注册表声明每个动作读取什么、写回什么、使用哪个提示词与上下文档位，Web 与 MCP 共用同一套能力边界。
- 人物支持主角/重要配角/次要角色/NPC/路人分级及参与章节范围，构建上下文时自动过滤尚未出场或已经退场的人物。
- 本地 35B 模型严格单并发，后台任务使用 SQLite 持久队列，刷新页面后恢复日志。
- 后台连续生成 1–10 章；质量或一致性异常自动暂停，问题正文保留为草稿。
- 人物新增和状态变化必须审核后才写入档案；硬事实、伏笔和时间线持续追踪。
- 章节覆盖前自动版本存档，可比较差异和恢复。
- 支持 TXT、Markdown、DOCX、EPUB、完整项目 ZIP 导出。
- 支持已有 TXT 小说导入、项目 ZIP 恢复、小说回收站和每日自动备份。
- 编辑器支持自动草稿、局部润色、扩写、增强对话和降低 AI 味。
- 全局 AI 运行现场、作品风险仪表盘、模型状态和硬件安全设置中心。

**AI 长篇小说工作空间管理系统** — 生产级 MCP Server，专为 AI 长篇小说连载创作设计。
支持多小说项目管理、按 LMS 实际模型窗口动态调整的分任务上下文预算、MCP 工具接口、向量语义检索、深度一致性检查。

## 核心设计

| 角色 | 职责 |
|------|------|
| **用户** | 世界观、总纲、人物设定、风格要求、剧情方向 |
| **AI (LLM)** | 章节生成（默认每章3000字 ≈ 31秒生成）、自动续写、结构化摘要、人物状态更新 |
| **MCP** | 46 个工具接口、事务性存储、向量检索、上下文裁剪（24K tokens 分 9 级优先级）、一致性检查 |

## 模型适配

已针对你的环境优化：

| 参数 | 值 |
|------|-----|
| 模型 | Ornith 1.0 35B AEON Ultimate Uncensored MTP APEX I Compact |
| 加载参数 | 完全沿用 LM Studio 当前实例，不由项目覆盖 |
| 上下文窗口 | 启动后从 LMS 实际加载值同步；未连接时按最近保存的 32K 保守预算 |
| 生成速度 | 以项目运行现场和性能基准的实测值为准 |
| 默认每章 | 5,000 字，最大 20,000 字 |
| 向量模型 | text-embedding-nomic-embed-text-v1.5 |
| API 地址 | http://127.0.0.1:1234/v1 |

> 请先在 LM Studio 中使用你保存的参数加载 Ornith。项目只启动/连接 LMS API，不覆盖上下文、GPU、CPU专家层、KV、Flash Attention、MTP和聊天模板；连接后会同步实际上下文窗口。

## MCP 工具（70 个）

### 📚 项目管理（4 个）
| 工具 | 功能 | 参数 |
|------|------|------|
| `list_novels` | 列出所有小说及状态/章节数 | - |
| `create_novel` | 创建新小说（自动生成世界观/大纲模板） | name, genre, style, description |
| `open_novel` | 切换当前工作小说 | name |
| `get_novel_status` | 查看完整状态：人物/最近剧情/伏笔/目标 | - |

### ✍️ 写作核心（7 个）
| 工具 | 功能 |
|------|------|
| `continue_story` | 【核心】自动准备下一章写作上下文（含世界观、大纲、摘要、人物、伏笔） |
| `save_chapter` | 保存章节 → 自动生成结构化摘要 → 扫描人物演变 → 触发插件事件 |
| `append_chapter` | 追加内容到已有章节（用于连续生成分段追加） |
| `read_chapter` | 读取指定章节全文 |
| `get_context` | 按 9 级优先级分配 token 构建上下文 |
| `update_next_goal` | 更新当前写作目标 |
| `update_novel_status` | 更新状态（创作中/暂停/大纲阶段/完成） |

### 👤 人物系统（5 个）
| 工具 | 功能 |
|------|------|
| `create_character` | 创建人物（含 17 级能力等级体系） |
| `update_character` | 更新人物属性，自动记录等级历史/位置 |
| `get_character` | 获取人物详细档案 |
| `list_characters` | 列出所有人物及等级/状态 |
| `get_character_network` | 获取人物关系图谱 |

### ⏱ 时间线（2 个）
| 工具 | 功能 |
|------|------|
| `add_event` | 添加事件（章节/时间/地点/人物） |
| `query_timeline` | 按人物/章节/关键词查询 |

### 🔍 检索与一致性（4 个）
| 工具 | 功能 |
|------|------|
| `check_consistency` | 深度检查：死亡复出、等级跳跃>2级/章、逆向突破、时间冲突、章节缺失、世界规则违反 |
| `search_memory` | 向量语义检索 → 关键词回退 |
| `index_chapter_to_vector` | 将章节加入向量索引 |
| `analyze_chapter` | 分析章节写作模式 |

### 📝 写作分析（3 个）
| 工具 | 功能 |
|------|------|
| `detect_writing_patterns` | AI 指纹检测（21 种模式：重复、高频词、句式单调等） |
| `analyze_text_pacing` | 文本节奏分析（段落长度、对话密度） |
| `extract_style_from_text` | 从文本提取写作风格特征 |

### 💾 版本管理（4 个）
| 工具 | 功能 |
|------|------|
| `create_savepoint` | 创建章节快照（Git 风格） |
| `list_savepoints` | 列出快照 |
| `restore_savepoint` | 恢复到指定版本 |
| `diff_savepoints` | 比较快照差异 |

### 🔌 插件系统（3 个）
| 工具 | 功能 |
|------|------|
| `list_plugins` | 列出已加载插件 |
| `reload_plugins` | 热重载 |
| `toggle_plugin` | 启用/禁用 |

### 📊 质量管理（3 个）
| 工具 | 功能 |
|------|------|
| `report_quality_issue` | 报告质量问题 |
| `get_quality_report` | 获取质量报告 |
| `get_pending_issues` | 获取待处理问题 |

### 🔄 人物演变（2 个）
| 工具 | 功能 |
|------|------|
| `scan_character_evolution` | 扫描章节中的人物变化 |
| `get_character_evolution` | 获取人物演变报告 |

### 🎨 风格预设（4 个）
| 工具 | 功能 |
|------|------|
| `list_style_presets` | 列出风格预设 |
| `get_style_preset` | 获取风格预设详情 |
| `save_style_preset` | 保存风格预设 |
| `extract_style_from_text` | 从文本提取风格特征 |

### 📦 批量生成（3 个）
| 工具 | 功能 |
|------|------|
批量生成统一由 Web 主页面的“连续章节生成”可恢复工作流执行，所有章节都经过草稿回合和正史提交。

### 🚀 会话管理（3 个）
| 工具 | 功能 |
|------|------|
| `where_was_i` | 查看上次写作进度 + 下一步建议 |
| `resume_session` | 生成新会话恢复包（冷启动全景概览） |
| `get_model_config` | 查看当前模型配置 |

## 快速开始

### 产品预览

![墨境本地写作工作台](docs/assets/web-studio.png)

![Novel Workspace MCP 架构图](docs/architecture/overview.svg)

[▶ 查看 11 秒产品预告视频（MP4）](docs/assets/demo.mp4) · [90 秒演示脚本](docs/demo.md)

中英日三语介绍： [English README](README.md) · [日本語 README](README.ja.md)

### 选择模型后端

复制 `.env.example` 为 `.env`，按需选择后端（`.env` 不会提交到 Git）：

```dotenv
# 本地 LM Studio（默认）
NOVEL_LLM_PROVIDER=local
NOVEL_LLM_BASE_URL=http://127.0.0.1:1234/v1

# 或远程 OpenAI-compatible API
NOVEL_LLM_PROVIDER=api
NOVEL_LLM_BASE_URL=https://your-provider.example/v1
NOVEL_LLM_API_KEY=替换为你的密钥
NOVEL_LLM_MODEL=你的模型 ID
NOVEL_LLM_EMBED_MODEL=你的嵌入模型 ID
```

API 密钥只放在环境变量或本地 `.env`，不要写入源码、README、截图或 Git 历史。API 模式不会执行 LM Studio 的本地进程管理命令。

源码目录运行时，数据默认保存在仓库的 `storage/`；通过 wheel/PyPI 安装时自动使用操作系统用户数据目录。可通过 `NOVEL_WORKSPACE_HOME` 指定独立数据目录。

```bash
cd C:\AI\mcp\novel-workspace-mcp
uv sync
uv run novel-workspace doctor
uv run novel-workspace mcp
```

### 启动单页创作工作台

```bash
cd C:\AI\mcp\novel-workspace-mcp
uv run novel-workspace serve
```

浏览器打开 `http://127.0.0.1:8765`。页面中的创作、设定、人物、章节和时间线均在同一个工作台内；首次点击 AI 功能时会连接并加载本地 LM Studio 模型。

也可以使用一键脚本或 Docker：

```powershell
.\scripts\start.ps1
```

```bash
docker compose up --build
```

`docker-compose.yml` 默认连接宿主机的 OpenAI-compatible endpoint；数据通过 `novel_workspace_storage` 卷持久化。若使用本地 LM Studio，请在 `.env` 中配置可被容器访问的地址。

### Codex Skill

Skill 源码位于 `skills/novel-workspace/`。将该目录安装到 Codex 的 skills 目录后，可用 `$novel-workspace` 触发；Skill 会指导 Codex 优先使用 MCP 工具，并遵守项目的状态、一致性和数据隔离约束。

例如在 Windows PowerShell 中（目标目录按你的 Codex 安装位置调整）：

```powershell
Copy-Item -Recurse -Force .\skills\novel-workspace "$env:CODEX_HOME\skills\novel-workspace"
```

### 长篇生产主链

- 创建小说采用“总纲与分卷骨架 → 逐卷节纲 → 开篇滚动细纲”，单卷结构失败只回退当前卷。
- 章节默认整章生成；复杂长章可在创作控制中心启用逐场景模式。
- 每章保存后用一次结构化抽取统一更新摘要、人物候选、事实、伏笔、连续性交接和状态提案。
- 有证据的低风险状态自动提交；冲突、死亡、复活、关系跳变等进入权威状态裁决。
- 正文按自然段全文分块索引，混合语义、关键词和章节新近度排序；全书搜索会解释召回原因。
- TXT 导入后进入串行后台重建，自动恢复总纲、人物、章节记忆和动态状态。
- 所有模型调用保存最终 Prompt 快照；可以在主界面建立基线并查看后续差异。
- 作者覆盖保存 AI 草稿时，仅学习段落、句长和对白比例等抽象偏好，不复制具体句子或剧情。
- 已发生剧情允许事务式修改：先扫描前置铺垫、事实发生章和后续结果，在隔离分支逐章修补；验证通过后原子提交并重建事实、人物认知、伏笔、实体和状态账本，任一步失败都会恢复修改前版本。

### 测试

```bash
uv run pytest -q
```

数据默认保存在 `storage/`：任务数据库为 `storage/tasks.db`，每日备份位于 `storage/backups/`，删除的小说位于 `storage/.trash/`。

## LM Studio MCP 配置

在 `C:\Users\admin\.lmstudio\mcp.json` 中添加：

```json
{
  "mcpServers": {
    "novel-workspace": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\AI\\mcp\\novel-workspace-mcp",
        "python",
        "novel_server.py"
      ],
      "env": {}
    }
  }
}
```

如果 MCP 使用远程 API，把同一段配置中的 `env` 改为：

```json
"env": {
  "NOVEL_LLM_PROVIDER": "api",
  "NOVEL_LLM_BASE_URL": "https://your-provider.example/v1",
  "NOVEL_LLM_API_KEY": "${NOVEL_LLM_API_KEY}",
  "NOVEL_LLM_MODEL": "your-model-id",
  "NOVEL_LLM_EMBED_MODEL": "your-embedding-model-id"
}
```

不同 MCP 宿主对 `${...}` 环境变量展开规则可能不同；不支持展开时，请在启动宿主进程的系统环境中注入密钥，不要把真实密钥写进 JSON。

## 推荐工作流

### 首次写作
1. `create_novel` → 自动生成世界观模板
2. 编辑 `bible/world.md`、`rules.md`、`style.md`
3. `create_character` 创建人物
4. `create_character` 创建更多人物
5. `update_next_goal` 设置目标
6. `continue_story` 获取续写上下文
7. 让 AI 生成正文 → `save_chapter` 保存

### 续写（新对话）
1. `where_was_i` 查看进度
2. `resume_session` 恢复上下文
3. `continue_story` 获取续写上下文
4. 生成正文 → `save_chapter`

### 质量维护
1. 每写 10 章：`check_consistency` 检查矛盾
2. 定期 `get_quality_report` 查看质量趋势
3. 人物等级突破时：`update_character` 更新等级

## 项目结构

```
novel-workspace-mcp/
├── novel_server.py          # MCP Server（78 个工具）
├── config.py                # 模型配置、上下文预算
├── storage_utils.py         # 事务性存储、备份、文件锁
├── llm_client.py            # LM Studio API 客户端
├── vector_store.py          # ChromaDB 向量搜索引擎
├── pyproject.toml
├── README.md / README.zh-CN.md / README.ja.md
├── core/
│   ├── workspace_manager.py # 多小说工作空间管理
│   ├── novel_manager.py     # 单小说状态管理
│   ├── chapter_manager.py   # 章节 CRUD + 事务写入
│   ├── summary_manager.py   # LLM 结构化摘要生成
│   ├── context_manager.py   # Token 预算上下文引擎（9 级优先级）
│   ├── ai_action_registry.py # AI 动作读写契约单一事实源
│   ├── scene_outline_manager.py # 场景级细纲与确认状态
│   ├── state_card_manager.py # 人物/地点/物品/势力动态状态卡
│   ├── canonical_state_manager.py # 状态提案、风险裁决和版本记录
│   ├── prompt_snapshot_manager.py # 最终Prompt快照与基线回归
│   ├── author_preference_manager.py # 作者修改的抽象偏好学习
│   ├── import_rebuilder.py # 旧小说分批拆解与工程重建
│   ├── history_revision_manager.py # 历史剧情双向影响、分支修补与原子提交
│   ├── planning_impact_manager.py # 规划修改传播与缓存失效
│   ├── genre_pack_manager.py # 题材结构、节奏和反模式方法包
│   ├── story_sandbox_manager.py # 隔离剧情候选与采纳
│   ├── long_form_evaluator.py # 无模型调用的长篇质量基线
│   ├── workflow_engine.py    # 可恢复工作流目录与载荷
│   ├── character_manager.py # 人物系统 + 17 级能力等级
│   ├── timeline_manager.py  # 时间线事件管理
│   ├── consistency_manager.py # 7 类深度一致性检查
│   ├── writing_analyzer.py  # 写作模式分析（21 种 AI 指纹）
│   ├── savepoint_manager.py # Git 风格版本快照
│   ├── plugin_manager.py    # 事件总线 + 插件热加载
│   ├── quality_tracker.py   # 跨章节质量追踪
│   ├── character_evolution.py # 人物演变追踪
│   ├── style_preset.py      # 写作风格预设管理
├── plugins/
│   └── chapter_stats.py     # 示例插件
├── storage/
│   ├── workspace.json       # 小说项目索引
│   └── novels/              # 小说数据
├── tests/
│   └── ...                   # 单元测试与工作流回归测试
├── skills/
│   └── novel-workspace/      # Codex Skill（SKILL.md + UI 元数据）
└── logs/
```

## 技术栈

- Python ≥3.10 + uv
- MCP SDK 1.x (mcp 协议)
- LM Studio API（文本生成 + 嵌入）
- ChromaDB（向量语义搜索）
- httpx（API 客户端）
- filelock（并发保护）
- RotatingFileHandler（日志轮转，10MB × 5）

## License

MIT
