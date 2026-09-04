# Changelog

All notable changes to Novel Workspace MCP are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use semantic versioning.

## [Unreleased]

### Planned

- Expand English and Japanese coverage for dynamically generated task messages.
- Add more provider adapters and real-user integration reports.

## [2.3.0] - 2026-09-04

### Added

- English-first GitHub and PyPI landing page with Chinese and Japanese editions.
- `novel-workspace` CLI with `serve`, `mcp`, `doctor`, and `--version` commands.
- `/healthz` and `/readyz` probes for containers and process supervisors.
- Automated GHCR container publishing for tagged releases.
- Docker health checks and release artifact attachment.

### Changed

- Runtime test tooling moved to the optional `dev` dependency extra.
- GitHub Actions upgraded to current Node 24-compatible major versions.
- Application startup and release logs now use English.

### Fixed

- Language switching now restores the original Chinese interface after selecting English or Japanese.
- GPU telemetry and process controls now degrade cleanly on Linux and macOS.
- Docker Compose starts without requiring a local `.env` file.
- Package, MCP server, and Web API versions now share a single source of truth.
- Installed wheels now store mutable data in the operating system's user data directory instead of `site-packages`.
- MCP diagnostics now use stderr, keeping the stdout JSON-RPC transport clean.

## [2.2.1] - 2026-09-02

### Added

- OpenAI-compatible API backend alongside local LM Studio.
- Codex Skill under `skills/novel-workspace/`.
- Public OSS documentation, contribution guide, security policy, and issue templates.

### Fixed

- Cross-platform LM Studio lifecycle handling.
- Safe packaging with explicit setuptools package discovery.
- Working-draft lock files no longer appear beside user content.
- MCP SDK constrained to the compatible 1.x API.

## [2.2.0] - 2026-09-02

### Added

- Three public entry points: Web Studio, MCP Server, and Codex Skill.
- Chapter planning, continuity checks, timeline, facts, foreshadowing, savepoints, recovery, and export workflows.
- Data-free public repository baseline with local runtime data excluded from Git.

[Unreleased]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.3.0...HEAD
[2.3.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.2.1...v2.3.0
[2.2.1]: https://github.com/LiZh132707/novel-workspace-mcp/releases/tag/v2.2.1
[2.2.0]: https://github.com/LiZh132707/novel-workspace-mcp/releases/tag/v2.2.0
