from types import SimpleNamespace

import pytest

import llm_client
from config import MODEL_CONFIG
from llm_client import LMStudioClient, MODEL_KEY


def test_managed_start_never_invokes_direct_server(monkeypatch):
    client = LMStudioClient()
    calls = []
    monkeypatch.setattr(client, "_start_server", lambda: calls.append("lms-server"))
    monkeypatch.setattr(client, "_start_direct_server", lambda: (_ for _ in ()).throw(AssertionError("不得直启")))
    monkeypatch.setattr(client, "_wait_ready", lambda _timeout: True)
    monkeypatch.setattr(client, "_sync_context_from_lms", lambda: calls.append("sync"))
    monkeypatch.setattr(client, "_apply_cpu_affinity", lambda: calls.append("affinity"))
    assert client.start(wait_ready=True) is True
    assert calls == ["lms-server", "sync", "affinity"]


def test_managed_start_reuses_actual_lms_server_port(monkeypatch):
    client = LMStudioClient()
    monkeypatch.setattr(client, "_discover_server_port", lambda: 1235)
    monkeypatch.setattr(
        llm_client.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("运行中的 LMS 不应重复启动")),
    )
    client._start_server()
    assert client.base_url == "http://127.0.0.1:1235"
    assert client._server_started is True


def test_discover_lms_server_port_from_json(monkeypatch):
    client = LMStudioClient()
    monkeypatch.setattr(
        llm_client.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout='{"running":true,"port":1235}', stderr="",
        ),
    )
    assert client._discover_server_port() == 1235


def test_managed_model_binds_to_eight_physical_cores(monkeypatch):
    client = LMStudioClient()
    commands = []
    monkeypatch.setattr(llm_client.os, "name", "nt")

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", run)
    assert client._apply_cpu_affinity() is True
    assert "ProcessorAffinity=[intptr]65535" in commands[0][-1]


def test_managed_wait_rejects_wrong_loaded_model(monkeypatch):
    client = LMStudioClient()
    monkeypatch.setattr(client, "_loaded_model_ids", lambda: ["other-writing-model"])
    monkeypatch.setattr(llm_client.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="其他模型"):
        client._wait_ready(3)


def test_managed_context_uses_actual_lms_instance_value(monkeypatch):
    client = LMStudioClient()
    monkeypatch.setitem(MODEL_CONFIG, "context_window", MODEL_CONFIG["context_window"])
    monkeypatch.setitem(MODEL_CONFIG, "max_input_tokens", MODEL_CONFIG["max_input_tokens"])
    monkeypatch.setitem(MODEL_CONFIG, "available_context", MODEL_CONFIG["available_context"])
    monkeypatch.setattr(client, "loaded_models", lambda: [{
        "identifier": MODEL_KEY, "contextLength": 65536,
    }])
    client._sync_context_from_lms()
    assert MODEL_CONFIG["context_window"] == 65536
    assert MODEL_CONFIG["available_context"] <= 65536


def test_project_stop_does_not_stop_lms_or_unload_models(monkeypatch):
    client = LMStudioClient()
    monkeypatch.setattr(
        llm_client.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stop不应调用LMS命令")),
    )
    client.stop()
    assert client._model_loaded is False


def test_explicit_unload_targets_only_ornith(monkeypatch):
    client = LMStudioClient()
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", run)
    assert client.unload_all() is True
    assert commands == [[llm_client.LMS_PATH, "unload", MODEL_KEY]]


def test_direct_server_path_is_disabled_in_lms_managed_mode():
    with pytest.raises(RuntimeError, match="LMS 托管模式"):
        LMStudioClient()._start_direct_server()
