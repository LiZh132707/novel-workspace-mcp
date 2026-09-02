# Community launch checklist

This project is prepared for real-world feedback, but it does not manufacture user activity. Please only open an Issue, Discussion, or PR when you have actually run the project or changed the code.

## Try it and report back

1. Start the web studio or MCP server with a local model or an OpenAI-compatible API.
2. Create a disposable test novel and run one chapter through the pipeline.
3. Share the OS, Python version, backend type, reproduction steps, and sanitized logs.

Useful first reports:

- Installation or provider compatibility on Linux/macOS/Windows
- Prompt/context quality for long chapters
- MCP client integration (Codex, Claude Desktop, LM Studio)
- Import/export edge cases and recovery behavior
- Documentation improvements or new provider adapters

Use the templates in `.github/ISSUE_TEMPLATE/`. Discussions are best for design questions; PRs should include tests and a short release note.
