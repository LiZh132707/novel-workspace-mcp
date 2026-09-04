# Changelog

All notable changes to Novel Workspace MCP are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use semantic versioning.

## [Unreleased]

### Planned

- Expand English and Japanese coverage for dynamically generated task messages.
- Add more provider adapters and real-user integration reports.

## [2.4.0] - 2026-09-04

### Added

- Optional Web Studio access-token protection with browser Basic authentication, API Bearer authentication, and a dedicated token header.
- Exact-origin CORS allowlists through `NOVEL_WEB_CORS_ORIGINS`, with invalid and wildcard entries surfaced by `doctor`.
- `novel-workspace config` for sanitized support diagnostics and `novel-workspace backup` for manual single-project or all-project archives.
- PEP 517 build metadata so `uv sync` installs the project and its console commands in a clean checkout.
- Codex Skill resources in wheel and source distributions, plus `novel-workspace skill-path` for reliable discovery.
- CI and release-package checks for sanitized configuration, backups, and installed Codex Skill resources.

### Changed

- Docker Compose now publishes the Web Studio to `127.0.0.1` by default and requires an explicit bind-address override for network exposure.
- Manual and scheduled backups verify archive CRCs before atomically publishing them.
- Security and environment documentation is English-first, with matching deployment guidance in the Chinese and Japanese READMEs.

### Fixed

- Removed the wildcard CORS and credential combination that browsers handle inconsistently and that was too broad for remote deployments.
- Fixed backup retention for project names containing glob metacharacters or sharing prefixes with another project.
- Fixed backup exclusion checks when a parent directory outside the project happens to be named `exports`.
- Prevented backup targets inside a novel project and excluded symbolic links from archives.
- Redacted embedded URL credentials, query parameters, and fragments from Web and CLI configuration reports.
- Kept Python 3.10 installations resolvable after newer ONNX Runtime releases dropped CPython 3.10 wheels.

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

[Unreleased]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.4.0...HEAD
[2.4.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.2.1...v2.3.0
[2.2.1]: https://github.com/LiZh132707/novel-workspace-mcp/releases/tag/v2.2.1
[2.2.0]: https://github.com/LiZh132707/novel-workspace-mcp/releases/tag/v2.2.0
