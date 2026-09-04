# Security Policy

## Supported versions

Security fixes are applied to the latest release. Reproduce a report against the newest version before submitting it whenever practical.

## Reporting a vulnerability

Do not disclose an exploitable issue in a public Issue. Prefer GitHub's private **Report a vulnerability** channel. If private reporting is not enabled, contact the maintainer first and include a minimal reproduction, affected version, impact, and suggested remediation.

Never include real API keys, private novel text, personal filesystem paths, or complete production logs. Revoke and rotate any credential that may already have been exposed.

## Scope

High-value reports include path traversal, import/export boundary violations, prompt or manuscript disclosure, credential handling, remote provider requests, task-queue state corruption, authentication or CORS errors, and integrity failures affecting canonical project state or backups.

## Deployment guidance

- Keep the default loopback bind for a single-user workstation.
- Configure `NOVEL_WEB_ACCESS_TOKEN` before exposing the Web Studio to a network.
- Use exact `NOVEL_WEB_CORS_ORIGINS`; wildcard origins are rejected.
- Put public deployments behind TLS and a maintained reverse proxy.
- Keep `.env`, `storage/`, logs, model files, and generated backups outside Git.

The liveness and readiness probes intentionally remain unauthenticated and return only service status, version, and provider type.
