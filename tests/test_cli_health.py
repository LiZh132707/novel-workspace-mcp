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
    assert {item["name"] for item in report["checks"]} >= {
        "python", "dependencies", "storage", "provider", "web_auth", "cors"
    }


def test_doctor_rejects_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr(config, "LLM_PROVIDER", "unknown")
    report = novel_cli.doctor(check_model=True)
    assert report["status"] == "fail"
    assert next(item for item in report["checks"] if item["name"] == "provider")["status"] == "fail"
    assert next(item for item in report["checks"] if item["name"] == "model")["status"] == "fail"


def test_config_command_is_machine_readable_and_sanitized(monkeypatch, capsys):
    monkeypatch.setattr(config, "LLM_API_KEY", "private-model-key")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://user:url-secret@api.example/v1?api_key=query-secret")
    monkeypatch.setattr(config, "WEB_ACCESS_TOKEN", "private-web-token")
    assert novel_cli.main(["config", "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["model"]["api_key_configured"] is True
    assert report["web"]["access_token_configured"] is True
    assert "private-model-key" not in output
    assert "private-web-token" not in output
    assert "url-secret" not in output
    assert "query-secret" not in output
    assert report["model"]["base_url"] == "https://api.example/v1"


def test_backup_command_creates_verified_archives(tmp_path, monkeypatch, capsys):
    novels = tmp_path / "storage" / "novels"
    novel = novels / "Demo"
    novel.mkdir(parents=True)
    (novel / "state.json").write_text("{}", "utf-8")
    (novel / "chapter.txt").write_text("chapter", "utf-8")
    monkeypatch.setattr(config, "STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr(config, "NOVELS_ROOT", novels)

    assert novel_cli.main(["backup", "--novel", "Demo", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["count"] == 1
    archive = Path(report["files"][0])
    assert archive.is_file()

    assert novel_cli.main(["backup", "--novel", "Missing", "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["status"] == "fail"


def test_skill_path_command_finds_complete_bundled_skill(capsys):
    assert novel_cli.main(["skill-path", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    skill = Path(report["path"])
    assert (skill / "SKILL.md").is_file()
    assert (skill / "agents" / "openai.yaml").is_file()


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
