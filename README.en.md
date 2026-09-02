# Novel Workspace MCP

[![CI](https://github.com/LiZh132707/novel-workspace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/LiZh132707/novel-workspace-mcp/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/LiZh132707/novel-workspace-mcp?display_name=tag&sort=semver)](https://github.com/LiZh132707/novel-workspace-mcp/releases)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**[中文](README.md) · English · [日本語](README.ja.md)**

> From a single idea to a complete long-form novel.

Novel Workspace MCP is a production-oriented AI writing workspace for long-form fiction. It keeps world-building, characters, chapter plans, timelines, facts, foreshadowing, context, and revisions in one auditable project state.

## Three ways to use it

1. **Local web studio** — a focused browser workspace for planning, drafting, reviewing, and exporting.
2. **MCP server** — a tool surface for LM Studio, Claude Desktop, Codex, and other MCP clients.
3. **Codex Skill** — install `skills/novel-workspace/` and invoke `$novel-workspace` for state-aware writing workflows.

## Model backends

Use a local LM Studio server by default, or connect to any OpenAI-compatible API. Set `NOVEL_LLM_PROVIDER=local` or `api` in a private `.env` file. API keys never belong in source code, screenshots, or Git history.

## What it does

- Guided novel creation: premise, world, rules, style, outline, volumes, opening plan, and characters.
- Chapter pipeline: brief → plan → draft → quality gate → summary → continuity handoff.
- Long-form continuity: facts, timeline, character arcs, foreshadowing, causal checks, canonical locks, and travel rules.
- Safe editing: working drafts, savepoints, diffs, recovery, imports, exports, and transactional history revision.
- Persistent background jobs with resumable logs and strict single-concurrency model access.

## Quick start

### Product preview

![Novel Workspace local writing studio](docs/assets/web-studio.png)

![Novel Workspace MCP architecture](docs/architecture/overview.svg)

[▶ Watch the 90-second product trailer (MP4)](docs/assets/demo.mp4) · [Demo storyboard](docs/demo.md)

See the lightweight [demo storyboard](docs/demo.md) and the [community launch checklist](COMMUNITY.md).

```bash
uv sync
uv run python ui/app.py
```

Open `http://127.0.0.1:8765`. For the MCP server:

```bash
uv run python novel_server.py
```

See [`.env.example`](.env.example) for local/API configuration and the [Chinese README](README.md) for the complete tool catalog.

For Docker or one-click startup, use `docker compose up --build` or `scripts/start.ps1` / `scripts/start.sh`. The included `publish.yml` workflow builds distributions and is ready for PyPI trusted publishing once the repository environment is configured.

## Project status

The repository is intentionally data-free for public development. Runtime storage, logs, model assets, and local secrets are ignored by Git. Contributions, issue reports, and new provider adapters are welcome; see [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md).

## License

MIT © LiZh132707
