# Codex for OSS maintenance plan

`novel-workspace-mcp` is prepared to use Codex as a transparent maintainer assistant. Codex will not impersonate users or manufacture community activity; every external contribution remains attributable to the person who made it.

## Planned Codex responsibilities

- **PR review**: check tests, cross-platform behavior, data isolation, API-key handling, and consistency across Web/MCP/Skill surfaces.
- **Issue triage**: reproduce installation and provider bugs, label scope and severity, request the smallest missing diagnostic, and close duplicates with a linked explanation.
- **Release automation**: prepare changelogs, verify the test matrix, build wheel/sdist artifacts, draft release notes, and publish only through the repository's reviewed GitHub workflow.

## Human approval boundary

Codex may propose or prepare changes, but maintainers review and merge code, approve releases, and decide how community reports are handled. No fake user, Issue, Discussion, PR, download, or adoption metric is created by automation.

## 中文说明

本项目会将 Codex 用于 PR review、Issue triage 和 release automation：检查跨平台兼容性、复现问题、维护测试矩阵、生成变更日志和发布构建产物。所有外部沟通、合并和发布仍由维护者审核；不会伪造用户、Issue、Discussion、PR 或使用数据。

## 日本語概要

Codex は PR レビュー、Issue のトリアージ、リリース自動化を支援します。コードのマージ、リリース承認、コミュニティ対応は人間のメンテナーが行い、架空のユーザー活動や Issue/PR は作成しません。
