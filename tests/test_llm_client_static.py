import json

import httpx
import pytest
import config

from llm_client import LMStudioClient, detect_generation_loop


def test_short_non_stream_request_respects_requested_max_tokens():
    bodies = []
    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "短标题"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        })
    client = LMStudioClient(base_url="http://test")
    client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    try:
        assert client.chat("系统", "起名", max_tokens=80, seed=1) == "短标题"
        assert bodies[0]["max_tokens"] == 80
        assert client.last_metrics["retry_count"] == 0
        assert "queue_wait_seconds" in client.last_metrics
    finally:
        client.close()


def test_non_retryable_4xx_is_not_retried():
    calls = 0
    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad request"})
    client = LMStudioClient(base_url="http://test")
    client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="API 调用失败"):
            client.chat("系统", "请求", max_tokens=20, seed=1)
        assert calls == 1
    finally:
        client.close()


def test_stream_request_respects_short_limit_and_collects_metrics():
    bodies = []
    payload = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n'
        'data: [DONE]\n\n'
    )
    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=payload.encode("utf-8"), headers={"content-type": "text/event-stream"})
    client = LMStudioClient(base_url="http://test")
    client._client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    try:
        assert "".join(client.chat_stream("系统", "问候", max_tokens=30)) == "你好"
        assert bodies[0]["max_tokens"] == 30
        assert client.last_metrics["completion_tokens"] == 2
        assert client.last_metrics["prompt_tokens"] == 4
    finally:
        client.close()


def test_generation_loop_detector_handles_repeated_units_without_flagging_normal_text():
    normal = "".join(f"第{index}段中人物采取了不同的行动，并得到线索{index}。" for index in range(20))
    assert detect_generation_loop(normal) == ""
    repeated = "这是一段会让模型陷入循环的固定输出片段"
    assert detect_generation_loop("前文" * 20 + repeated * 4)


def test_managed_server_path_requires_exact_llama_server_executable():
    from llm_client import LLAMA_SERVER_PATH
    assert LMStudioClient._is_expected_server_path(LLAMA_SERVER_PATH)
    assert not LMStudioClient._is_expected_server_path(r"C:\Windows\System32\notepad.exe")


def test_api_mode_uses_openai_compatible_endpoint_and_auth_without_local_only_fields():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "远程结果"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        })

    client = LMStudioClient(
        provider="api",
        base_url="http://test/v1",
        api_key="secret",
        model="gpt-test",
        embed_model="embed-test",
    )
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer secret"})
    try:
        assert client.start() is True
        assert client.chat("系统", "请求", max_tokens=32, seed=7) == "远程结果"
        payload = json.loads(requests[-1].content)
        assert requests[-1].headers["authorization"] == "Bearer secret"
        assert payload["model"] == "gpt-test"
        assert payload["seed"] == 7
        assert "top_k" not in payload
        assert "repeat_penalty" not in payload
        assert "chat_template_kwargs" not in payload
    finally:
        client.close()


def test_api_mode_rejects_service_without_configured_model():
    def handler(_request: httpx.Request):
        return httpx.Response(200, json={"data": [{"id": "different-model"}]})

    client = LMStudioClient(provider="api", base_url="http://test/v1", model="wanted-model")
    client._client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    try:
        assert client.is_available() is False
    finally:
        client.close()


def test_dotenv_loader_is_optional_and_does_not_override_existing_environment(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("NOVEL_TEST_DOTENV='from-file'\nNOVEL_TEST_EXISTING=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "__file__", str(tmp_path / "config.py"))
    monkeypatch.setenv("NOVEL_TEST_EXISTING", "from-environment")
    monkeypatch.delenv("NOVEL_TEST_DOTENV", raising=False)
    config._load_dotenv()
    assert config.os.environ["NOVEL_TEST_DOTENV"] == "from-file"
    assert config.os.environ["NOVEL_TEST_EXISTING"] == "from-environment"
