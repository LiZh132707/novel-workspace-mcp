---
name: novel-workspace
description: Use the novel-workspace MCP server to plan, draft, revise, and maintain continuity for long-form fiction projects. Apply when the user asks to create or continue a novel, manage characters or timelines, review consistency, or recover a writing session.
---

# Novel Workspace

Use the project's MCP tools as the source of truth for novel state. Keep generated novels, databases, vector indexes, prompt snapshots, logs, and backups in the local `storage/` and `logs/` directories; never add those runtime files to a commit.

## Writing workflow

- Recover context first with `where_was_i` or `resume_session` when continuing an existing project.
- For a new project, use `create_novel`, then establish the world bible, rules, style, outline, characters, and next goal before drafting.
- Generate with `continue_story`; commit finished prose with `save_chapter`. Treat working drafts as provisional until accepted.
- After meaningful changes, run `check_consistency`, inspect quality issues, and update character, timeline, fact, and foreshadowing records through their MCP tools.
- When revising published history, use the history-revision/planning-impact workflow so downstream summaries and state ledgers are rebuilt instead of editing derived files directly.

## Model providers

The server supports both local LM Studio and remote OpenAI-compatible APIs. Select the backend with environment variables before starting the server:

```text
NOVEL_LLM_PROVIDER=local   # default; LM Studio at http://127.0.0.1:1234
NOVEL_LLM_PROVIDER=api     # remote /v1-compatible endpoint
NOVEL_LLM_BASE_URL=https://example.invalid/v1
NOVEL_LLM_API_KEY=         # keep this outside Git
NOVEL_LLM_MODEL=your-model-id
NOVEL_LLM_EMBED_MODEL=your-embedding-model-id
```

In API mode, use only standard OpenAI-compatible request fields; local-only LM Studio lifecycle commands and Windows process management must remain disabled. If the model service is unavailable, report the exact provider and endpoint configuration needed instead of fabricating generated content.

## Repository work

For code changes, edit source and tests, not files under `storage/`. Preserve the public-repository boundary: no API keys, personal paths, model binaries, private manuscripts, logs, or local databases. Run the project's test suite after dependency setup and mention any environment-only limitation.
