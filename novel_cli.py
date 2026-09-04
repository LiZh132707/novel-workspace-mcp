"""Command-line entry point for Novel Workspace MCP."""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import uuid
from pathlib import Path

from version import __version__


def _check_dependencies() -> tuple[bool, str]:
    required = ("fastapi", "httpx", "mcp", "uvicorn")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    return (not missing, "available" if not missing else "missing: " + ", ".join(missing))


def _check_storage(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".doctor-{uuid.uuid4().hex}.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(path)
    except OSError as exc:
        return False, str(exc)


def doctor(*, check_model: bool = False) -> dict:
    """Return a machine-readable installation and configuration report."""
    import config

    dependency_ok, dependency_detail = _check_dependencies()
    storage_ok, storage_detail = _check_storage(config.STORAGE_ROOT)
    provider_ok = config.LLM_PROVIDER in {"local", "api"}
    web = config.get_web_config_report()
    checks = [
        {"name": "python", "status": "pass" if sys.version_info >= (3, 10) else "fail", "detail": sys.version.split()[0]},
        {"name": "dependencies", "status": "pass" if dependency_ok else "fail", "detail": dependency_detail},
        {"name": "storage", "status": "pass" if storage_ok else "fail", "detail": storage_detail},
        {"name": "provider", "status": "pass" if provider_ok else "fail", "detail": config.LLM_PROVIDER},
        {
            "name": "web_auth",
            "status": "warning" if web["access_token_configured"] and not web["access_token_recommended_length"] else "pass",
            "detail": (
                "configured (use at least 16 characters)"
                if web["access_token_configured"] and not web["access_token_recommended_length"]
                else "configured"
                if web["access_token_configured"]
                else "disabled; local-only binding is the default"
            ),
        },
        {
            "name": "cors",
            "status": "fail" if web["invalid_cors_origins"] else "pass",
            "detail": (
                "invalid: " + ", ".join(web["invalid_cors_origins"])
                if web["invalid_cors_origins"]
                else ", ".join(web["cors_origins"]) or "same-origin only"
            ),
        },
    ]
    if check_model and provider_ok:
        from llm_client import LMStudioClient

        client = LMStudioClient()
        try:
            model_ok = client.is_available()
            checks.append({
                "name": "model",
                "status": "pass" if model_ok else "warning",
                "detail": client.model_key if model_ok else f"No model available at {config.sanitize_service_url(client.base_url)}",
            })
        finally:
            client.close()
    elif check_model:
        checks.append({"name": "model", "status": "fail", "detail": "provider configuration is invalid"})
    failed = any(item["status"] == "fail" for item in checks)
    return {"status": "fail" if failed else "pass", "version": __version__, "checks": checks}


def _print_doctor(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"Novel Workspace MCP {report['version']} · doctor: {report['status'].upper()}")
    for item in report["checks"]:
        marker = {"pass": "OK", "warning": "WARN", "fail": "FAIL"}[item["status"]]
        print(f"[{marker}] {item['name']}: {item['detail']}")


def configuration_report() -> dict:
    """Return a concise configuration report that never contains secrets."""
    import config

    return {
        "version": __version__,
        "runtime_root": str(config.RUNTIME_ROOT),
        "storage_root": str(config.STORAGE_ROOT),
        "model": {
            "provider": config.LLM_PROVIDER,
            "base_url": config.sanitize_service_url(config.LLM_BASE_URL),
            "model": config.LLM_MODEL,
            "embedding_model": config.LLM_EMBED_MODEL,
            "timeout_seconds": config.LLM_TIMEOUT,
            "api_key_configured": bool(config.LLM_API_KEY),
        },
        "web": config.get_web_config_report(),
    }


def _print_configuration(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    model = report["model"]
    web = report["web"]
    print(f"Novel Workspace MCP {report['version']} · configuration")
    print(f"Runtime: {report['runtime_root']}")
    print(f"Storage: {report['storage_root']}")
    print(f"Model: {model['provider']} · {model['model']} · {model['base_url']}")
    print(f"API key: {'configured' if model['api_key_configured'] else 'not configured'}")
    print(f"Web access token: {'configured' if web['access_token_configured'] else 'not configured'}")
    print(f"CORS origins: {', '.join(web['cors_origins']) or 'same-origin only'}")


def create_backups(*, novel_name: str | None = None, output_dir: Path | None = None) -> dict:
    """Create verified project archives for one novel or every local novel."""
    import config
    from core.backup_manager import BackupScheduler

    entries = config.NOVELS_ROOT.iterdir() if config.NOVELS_ROOT.exists() else ()
    candidates = sorted(
        path for path in entries if path.is_dir() and (path / "state.json").is_file()
    )
    if novel_name is not None:
        candidates = [path for path in candidates if path.name == novel_name]
        if not candidates:
            return {"status": "fail", "error": f"Novel project not found: {novel_name}", "files": []}

    scheduler = BackupScheduler(
        config.NOVELS_ROOT,
        config.STORAGE_ROOT,
        logging.getLogger("novel-workspace.backup"),
        output_dir=output_dir,
    )
    files = [str(scheduler.create(path).resolve()) for path in candidates]
    return {"status": "pass", "count": len(files), "files": files}


def _print_backups(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    if report["status"] != "pass":
        print(report["error"], file=sys.stderr)
        return
    print(f"Created {report['count']} backup archive(s).")
    for path in report["files"]:
        print(path)


def bundled_skill_path() -> Path:
    """Locate the Codex Skill in a source checkout or an installed wheel."""
    candidates = (
        Path(__file__).resolve().parent / "skills" / "novel-workspace",
        Path(sys.prefix) / "share" / "novel-workspace-mcp" / "skills" / "novel-workspace",
    )
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file() and (candidate / "agents" / "openai.yaml").is_file():
            return candidate.resolve()
    raise FileNotFoundError("the bundled novel-workspace Codex Skill is missing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel-workspace", description="Novel Workspace MCP command line")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    serve = commands.add_parser("serve", help="start the local web studio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--reload", action="store_true")

    commands.add_parser("mcp", help="start the MCP server over stdio")
    diagnose = commands.add_parser("doctor", help="validate the installation and configuration")
    diagnose.add_argument("--check-model", action="store_true", help="also probe the configured model endpoint")
    diagnose.add_argument("--json", action="store_true", help="emit JSON for scripts and CI")
    show_config = commands.add_parser("config", help="show paths and sanitized runtime configuration")
    show_config.add_argument("--json", action="store_true", help="emit JSON for scripts and support reports")
    backup = commands.add_parser("backup", help="create portable backup archives for novel projects")
    backup.add_argument("--novel", help="back up one exact project name instead of every project")
    backup.add_argument("--output-dir", type=Path, help="write archives to this directory")
    backup.add_argument("--json", action="store_true", help="emit JSON for scripts")
    skill_path = commands.add_parser("skill-path", help="print the bundled Codex Skill directory")
    skill_path.add_argument("--json", action="store_true", help="emit JSON for scripts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        uvicorn.run("ui.app:app", host=args.host, port=args.port, reload=args.reload)
        return 0
    if args.command == "mcp":
        from novel_server import run

        run()
        return 0
    if args.command == "doctor":
        report = doctor(check_model=args.check_model)
        _print_doctor(report, args.json)
        return 0 if report["status"] == "pass" else 1
    if args.command == "config":
        _print_configuration(configuration_report(), args.json)
        return 0
    if args.command == "backup":
        try:
            report = create_backups(novel_name=args.novel, output_dir=args.output_dir)
        except (OSError, ValueError) as exc:
            report = {"status": "fail", "error": str(exc), "files": []}
        _print_backups(report, args.json)
        return 0 if report["status"] == "pass" else 1
    if args.command == "skill-path":
        try:
            path = str(bundled_skill_path())
        except OSError as exc:
            if args.json:
                print(json.dumps({"status": "fail", "error": str(exc)}, ensure_ascii=False))
            else:
                print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"status": "pass", "path": path}, ensure_ascii=False))
        else:
            print(path)
        return 0
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
