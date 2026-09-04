"""Command-line entry point for Novel Workspace MCP."""
from __future__ import annotations

import argparse
import importlib.util
import json
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
    checks = [
        {"name": "python", "status": "pass" if sys.version_info >= (3, 10) else "fail", "detail": sys.version.split()[0]},
        {"name": "dependencies", "status": "pass" if dependency_ok else "fail", "detail": dependency_detail},
        {"name": "storage", "status": "pass" if storage_ok else "fail", "detail": storage_detail},
        {"name": "provider", "status": "pass", "detail": config.LLM_PROVIDER},
    ]
    if check_model:
        from llm_client import LMStudioClient

        client = LMStudioClient()
        try:
            model_ok = client.is_available()
            checks.append({
                "name": "model",
                "status": "pass" if model_ok else "warning",
                "detail": client.model_key if model_ok else f"No model available at {client.base_url}",
            })
        finally:
            client.close()
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
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
