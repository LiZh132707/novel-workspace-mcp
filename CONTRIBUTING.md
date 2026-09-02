# Contributing

感谢参与 `novel-workspace-mcp`。网页工作台、MCP Server 和 Codex Skill 共用同一套核心状态模型，提交改动时请保持三种入口的行为一致。

## 本地开发

```powershell
uv sync
uv run pytest -q
```

模型服务不是测试前置条件。单元测试应使用 fake client 或 mock transport，不要把真实 API 密钥、小说正文、数据库、日志或模型文件提交到仓库。

## Pull Request

- 一个 PR 聚焦一个问题或一个功能。
- 新行为必须补回归测试；修 bug 时先写能复现问题的测试。
- 更新用户可见行为时同步修改 README 或 `.env.example`。
- 提交前确认 `git diff --check` 通过，并检查没有 `storage/`、`.env`、日志或本地模型资产。
- PR 描述写清楚动机、验证命令和已知限制。

## 模型后端

通过 `NOVEL_LLM_PROVIDER=local|api` 选择本地 LM Studio 或 OpenAI-compatible API。API 密钥只能通过环境变量注入，不能写入源码、测试样例或 CI 日志。

## Codex 维护边界

本项目明确将 Codex 用于 **PR review、Issue triage、release automation**：辅助检查测试、复现问题、整理变更日志和构建发布产物。Codex 不伪造用户活动；外部沟通、合并代码和发布版本仍由维护者审核。完整说明见 [`CODEX_OSS.md`](CODEX_OSS.md)。
