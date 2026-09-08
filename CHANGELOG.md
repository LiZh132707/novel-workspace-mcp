# Changelog

All notable changes to Novel Workspace MCP are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions use semantic versioning.

## [Unreleased]

### Planned

- Expand English and Japanese coverage for dynamically generated task messages.
- Add more provider adapters and real-user integration reports.

## [2.10.0] - 2026-09-08

### Added

- A project backup browser in Web Studio with manual creation, inventory metadata, read-only integrity verification, and project-scoped ZIP downloads.
- `novel-workspace backup --list` and `backup --verify FILENAME`, including JSON output and nonzero exit status for failed verification.
- Verification reports with SHA-256, file counts, expanded sizes, CRC checks, portable-path checks, and bounded JSON project-state validation. Verification never extracts or restores files.
- English, Chinese, and Japanese backup controls and usage documentation.

### Fixed

- Reject zero, negative, or non-integer retention values before a newly created backup can be removed.
- Select the latest backup chronologically across project names rather than by filename ordering.
- Reject malformed project states during backup creation before replacing or pruning older good archives.
- Exclude unmanaged filenames and symbolic-link archives from backup inventory and downloads.

### Tests

- Added regression coverage for retention, ordering, corrupt archives, path validation, CLI inspection, project-scoped APIs, downloads, and escaped frontend rendering.

## [2.9.0] - 2026-09-08

### Added

- A shared Web/MCP relationship inspector with directed edges, strength scores, chronological evidence, focal-character and NPC/role filters, and as-of-chapter queries.
- English, Chinese, and Japanese relationship controls, explicit pagination for large networks, and separate unresolved profile notes instead of invented character nodes.
- Compatible parsing of simple legacy profile relationships using Western or Chinese separators; chapter queries exclude undated profile prose.

### Fixed

- Backfilled chapters no longer override newer relationship observations in generation context.
- Relationship ingestion now skips entries explicitly marked `evidence_verified=False` and handles non-finite strength values without crashing.
- The relationship view retains durable older edges instead of showing only the last 30 change records.
- Invalid character sorting metadata and stored-name mismatches no longer break roster/network reads.

### Tests

- Verified 402 Python tests and 9 frontend tests, including chapter backfills, directed relationships, NPC appearance ranges, legacy profile compatibility, and Web/MCP parity.
- Manually verified relationship filtering, historical evidence, and multilingual controls in an isolated Web Studio workspace.

## [2.8.0] - 2026-09-08

### Added

- Web Studio style preset browser with full previews, explicit built-in/custom selection, and append/replace actions that stage changes without saving automatically.
- English, Chinese, and Japanese controls for the preset workflow, including confirmation before replacing existing style instructions.
- Read-only preset listing and preview APIs, plus MCP `source`, `prefer_custom`, and `include_rendered` options using the same Markdown renderer.
- A tested release-note extractor that publishes only the requested version, excluding planned work and historical announcements.

### Fixed

- Skip malformed custom preset records and use filenames as stable identities instead of trusting stored names.
- Isolate returned built-in preset lists from mutation and prevent explicit source selection from silently falling back.
- Ignore stale preview responses after selection or project changes.
- Synchronize the package lockfile version and replace stale README test counts with suite-based descriptions.

### Tests

- Added Python coverage for preset sources, malformed data, project lookup, Web/MCP preview parity, and version-specific release notes.
- Added frontend regression tests for append/replace behavior and enabled them in CI.

## [2.7.0] - 2026-09-07

### Added

- `prefer_custom=True` now explicitly selects a custom style preset when it shares a name with a built-in preset.

### Fixed

- Clamped vector-search `top_k` to a safe range and handled invalid values deterministically.

### Tests

- Added regression coverage for style precedence and invalid search limits.

## [2.6.0] - 2026-09-07

### Fixed

- Validated project names stored in trash metadata before restore or compensation, preventing restore-path traversal.
- Escaped dashboard labels and values before rendering them into HTML.

### Tests

- Added regression coverage for malicious trash metadata and dashboard rendering boundaries.

## [2.5.0] - 2026-09-07

### Fixed

- Hardened Web Studio project resolution so unregistered or escaping novel names cannot access storage outside the novels root.
- Validated character names on every read and mutation path to prevent filename traversal.
- Prevented a timed-out shutdown from re-queuing a handler that later completed, avoiding duplicate side effects.
- Preserved proxy environment configuration for API users instead of deleting process-wide proxy variables.
- Excluded symbolic links from complete-project ZIP exports and rejected malformed `state.json` project archives.
- Kept vector-search results from different novels from colliding when searching across the whole index.
- Returned protocol-level MCP tool errors with `isError=true`.

### Tests

- Added regression coverage for traversal, malformed imports, and late task completion during shutdown.

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

[Unreleased]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.10.0...HEAD
[2.10.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.9.0...v2.10.0
[2.9.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.8.0...v2.9.0
[2.8.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.7.0...v2.8.0
[2.5.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.4.0...v2.5.0
[2.6.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.5.0...v2.6.0
[2.7.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.6.0...v2.7.0
[2.4.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/LiZh132707/novel-workspace-mcp/compare/v2.2.1...v2.3.0
[2.2.1]: https://github.com/LiZh132707/novel-workspace-mcp/releases/tag/v2.2.1
[2.2.0]: https://github.com/LiZh132707/novel-workspace-mcp/releases/tag/v2.2.0
