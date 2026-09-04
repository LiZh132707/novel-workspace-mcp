# Novel Workspace MCP

[![CI](https://github.com/LiZh132707/novel-workspace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/LiZh132707/novel-workspace-mcp/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/LiZh132707/novel-workspace-mcp?display_name=tag&sort=semver)](https://github.com/LiZh132707/novel-workspace-mcp/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**[English](README.md) · [中文](README.zh-CN.md) · 日本語**

> ひとつのアイデアから、長編小説へ。

Novel Workspace MCP は、長編フィクションのための本格的な AI 執筆ワークスペースです。世界観、登場人物、章構成、タイムライン、事実、伏線、コンテキスト、改稿履歴を、検証可能なひとつのプロジェクト状態として管理します。

## 3 つの利用方法

1. **ローカル Web スタジオ** — 構成、執筆、レビュー、書き出しを行うブラウザ UI。
2. **MCP サーバー** — LM Studio、Claude Desktop、Codex などの MCP クライアントから利用。
3. **Codex Skill** — `skills/novel-workspace/` をインストールし、`$novel-workspace` で状態を理解した執筆フローを起動。ソース版とパッケージ版の場所は `novel-workspace skill-path` で確認できます。

## モデルバックエンド

既定ではローカルの LM Studio に接続し、OpenAI 互換 API にも切り替えられます。非公開の `.env` で `NOVEL_LLM_PROVIDER=local` または `api` を設定してください。API キーをソースコード、スクリーンショット、Git 履歴に保存しないでください。

ソースチェックアウトではリポジトリ内に実行データを保存し、パッケージ版では OS のユーザーデータディレクトリを使用します。保存先は `NOVEL_WORKSPACE_HOME` で指定できます。

`novel-workspace config --json` はシークレット本体を含まない診断情報を出力します。`novel-workspace backup` は全作品の CRC 検証済みアーカイブを作成し、`--novel NAME` でひとつの作品だけを選択できます。

## 主な機能

- アイデアから世界観、ルール、文体、全体構成、分巻、冒頭計画、登場人物まで段階的に作成。
- 章生成パイプライン：要約 → 計画 → 下書き → 品質ゲート → 要約 → 継続性引き継ぎ。
- 長編の整合性：事実、タイムライン、人物アーク、伏線、因果関係、正典ロック、移動ルール。
- 安全な編集：作業下書き、セーブポイント、差分、復旧、インポート、エクスポート、トランザクション改稿。
- 再開可能なバックグラウンドジョブと、モデルへの厳格な単一同時実行。

## クイックスタート

### プレビュー

![Novel Workspace ローカル執筆スタジオ](docs/assets/web-studio.png)

![Novel Workspace MCP アーキテクチャ](docs/architecture/overview.svg)

[▶ 11 秒のプロダクト予告（MP4）](docs/assets/demo.mp4) · [90 秒デモ台本](docs/demo.md)

```bash
uv sync
uv run novel-workspace doctor
uv run novel-workspace config
uv run novel-workspace serve
```

ブラウザで `http://127.0.0.1:8765` を開きます。MCP サーバーは次のコマンドで起動できます。

Web スタジオと Docker Compose は既定でループバックだけに公開されます。LAN やリバースプロキシからアクセスする場合は、16 文字以上のランダムな `NOVEL_WEB_ACCESS_TOKEN` を先に設定してください。ブラウザは HTTP Basic（ユーザー名 `novel`、パスワードはトークン）、API クライアントは Bearer または `X-Novel-Workspace-Token` を利用できます。別オリジンのフロントエンドには `NOVEL_WEB_CORS_ORIGINS` で完全一致の許可リストを設定します。

```bash
uv run novel-workspace mcp
```

ローカル/API 設定は [`.env.example`](.env.example) を、ツール一覧と詳細仕様は [中国語 README](README.zh-CN.md) を参照してください。

Docker は `docker compose up --build`、ワンクリック起動は `scripts/start.ps1` または `scripts/start.sh` を使用できます。Compose の公開先は既定で `127.0.0.1` です。CI は Python 3.10/3.12 で 366 件の自動テストを実行します。`publish.yml` は PyPI Trusted Publishing 用の配布物ビルドを備えています。

## プロジェクトの状態

公開開発用に、リポジトリには個人の執筆データを含めていません。実行時ストレージ、ログ、モデル資産、ローカルシークレットは Git の対象外です。コントリビューションは [`CONTRIBUTING.md`](CONTRIBUTING.md)、セキュリティ報告は [`SECURITY.md`](SECURITY.md) を参照してください。

## ライセンス

MIT © LiZh132707
