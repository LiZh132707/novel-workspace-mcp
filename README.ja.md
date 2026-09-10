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

### v2.12 伏線プランナー

**タイムライン → 伏線プランナー** から、伏線の追加・編集・延期・回収・取消・再開ができます。期限、状態、優先度、タグ、キーワードで絞り込み、根拠と直近 50 件の変更を確認できます。表示ページの JSON コピーと英語・中国語・日本語の操作表示に対応します。

手動で作成・編集した記録は作者管理となり、派生状態の再構築後も保持され、自動回収されません。自動回収は一意の ID または本文の完全一致のみを使用します。MCP の `get_foreshadow_board`、`create_foreshadow`、`update_foreshadow` は同じ機能を提供し、編集競合の検出にも対応します。[仕様と使用例](docs/FORESHADOW_PLANNER.md) を参照してください。

### v2.11 原稿診断ワークベンチ

**ダッシュボード → 原稿診断** で章の長さの推移、目標に対する割合、推定読書時間、欠落・空白の章、ファイル名の競合、章間で重複した行の位置を確認できます。章範囲の絞り込みと JSON / Markdown 出力に対応します。操作表示は英語・中国語・日本語、構造化された検出内容と出力レポートは英語です。

CLI の `novel-workspace inspect --novel NAME --json` と MCP の `inspect_manuscript(start_chapter=1, end_chapter=20)` でも同じ読み取り専用エンジンを利用できます。モデルを呼び出さず、本文や保存済み文字数は変更しません。抜粋は既定で無効です。漢字・仮名と文字/数字の単語を単位として数え、空白を正規化した非空行を完全一致で比較します。[指標・制限・使用例](docs/MANUSCRIPT_DIAGNOSTICS.md) を参照してください。

### v2.10 バックアップ管理

全体検索の隣にある **プロジェクトのバックアップ** から、作成・一覧表示・整合性検証・ZIP ダウンロードができます。検証は展開や上書きを行いません。復元する場合はコピーをダウンロードし、インポート機能で別のプロジェクトとして作成してください。

CLI は `novel-workspace backup --list --json` と `novel-workspace backup --verify FILENAME --json` に対応し、`--output-dir` で対象ディレクトリを指定できます。検証対象は CRC、パス、JSON 状態などです。SHA-256 も表示します。上限は 10,000 エントリ、展開時 1 GB、状態ファイル 2 MB です。検証成功は物語の完全性・真正性や完全な復元を保証しません。

### NPC と人物関係

**登場人物 → 人物関係図** で有向の関係、強度（-100〜100）、章ごとの根拠履歴を確認できます。中心人物、役割（NPC を含む）、対象章までの絞り込みに対応します。NPC の関係先も表示され、登場範囲は現在のプロフィールを使用します。

MCP の `get_character_network(chapter=10, role_tier="NPC")` でも同じ結果を取得できます。章未指定の説明は最新表示だけに含め、解析できない説明は注記として残します。現在の役割・状態は完全な過去のスナップショットではありません。観察記録は作者の確認を代替しません。

### v2.8 文体プリセット

**ストーリーバイブル → 文体プリセット** で組み込み・カスタムの指示をプレビューし、文体編集欄への追加または置換ができます。自動保存は行いません。内容を確認し、**設定を保存** で確定してください。既存内容の置換には確認が必要です。操作表示は英語・中国語・日本語に対応し、プリセット本文は元の言語を維持します。

MCP の `get_style_preset(name, source="custom", include_rendered=True)` でも同じ Markdown を取得できます。`source` は `auto`、`builtin`、`custom` に対応し、明示したソースから別のソースへのフォールバックは行いません。

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

Docker は `docker compose up --build`、ワンクリック起動は `scripts/start.ps1` または `scripts/start.sh` を使用できます。Compose の公開先は既定で `127.0.0.1` です。CI は Python 3.10/3.12 とフロントエンドの回帰テストを実行します。`publish.yml` は PyPI Trusted Publishing 用の配布物ビルドを備えています。

## プロジェクトの状態

公開開発用に、リポジトリには個人の執筆データを含めていません。実行時ストレージ、ログ、モデル資産、ローカルシークレットは Git の対象外です。コントリビューションは [`CONTRIBUTING.md`](CONTRIBUTING.md)、セキュリティ報告は [`SECURITY.md`](SECURITY.md) を参照してください。

## ライセンス

MIT © LiZh132707
