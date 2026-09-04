import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import config
import novel_cli
from ui import app as web
from version import __version__


def test_version_is_consistent_across_package_server_and_web_app():
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8")
    project_version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert project_version and project_version.group(1) == __version__
    assert config.SERVER_VERSION == __version__
    assert web.app.version == __version__


def test_doctor_reports_machine_readable_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path / "storage")
    assert novel_cli.main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "pass"
    assert report["version"] == __version__
    assert {item["name"] for item in report["checks"]} >= {"python", "dependencies", "storage", "provider"}


def test_runtime_home_can_be_overridden_for_installed_and_container_runs(tmp_path, monkeypatch):
    runtime_home = tmp_path / "runtime-home"
    monkeypatch.setenv("NOVEL_WORKSPACE_HOME", str(runtime_home))
    assert config._default_runtime_root(tmp_path / "installed-package") == runtime_home.resolve()


def test_console_logging_uses_stderr_to_protect_mcp_stdio(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr(config, "NOVELS_ROOT", tmp_path / "storage" / "novels")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    logger = config.setup_logging()
    console = [handler for handler in logger.handlers if type(handler) is logging.StreamHandler]
    assert len(console) == 1
    assert console[0].stream is sys.stderr


def test_health_and_readiness_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path / "storage")
    health = asyncio.run(web.healthz())
    ready = asyncio.run(web.readyz())
    assert health == {"status": "ok", "service": "novel-workspace", "version": __version__}
    assert ready.status_code == 200
    assert json.loads(ready.body)["status"] == "ready"


def test_non_windows_gpu_features_degrade_cleanly(monkeypatch):
    monkeypatch.setattr(web, "_is_windows", lambda: False)
    assert web._gpu_processes() == []
    response = asyncio.run(web.api_hardware_stats())
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["stats"]["utilization"] is None
    assert "Windows only" in payload["stats"]["note"]
