import asyncio
import base64

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from ui.security import AccessTokenMiddleware


def _protected_app(token: str = "correct-horse-battery-staple") -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def index():
        return {"status": "ok"}

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    app.add_middleware(AccessTokenMiddleware, token=token)
    return app


def _request(app: FastAPI, method: str, path: str, headers: dict | None = None) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, headers=headers)

    return asyncio.run(send())


def test_access_token_gate_supports_browser_and_api_clients():
    app = _protected_app()
    assert _request(app, "GET", "/").status_code == 401
    assert _request(app, "GET", "/healthz").status_code == 200
    assert _request(app, "GET", "/", {"Authorization": "Bearer wrong"}).status_code == 401
    assert _request(
        app, "GET", "/", {"Authorization": "Bearer correct-horse-battery-staple"}
    ).status_code == 200
    assert _request(
        app, "GET", "/", {"X-Novel-Workspace-Token": "correct-horse-battery-staple"}
    ).status_code == 200

    basic = base64.b64encode(b"novel:correct-horse-battery-staple").decode("ascii")
    assert _request(app, "GET", "/", {"Authorization": f"Basic {basic}"}).status_code == 200
    assert _request(app, "GET", "/", {"Authorization": "Basic not-base64"}).status_code == 401


def test_access_token_gate_is_a_noop_when_not_configured():
    assert _request(_protected_app(token=""), "GET", "/").status_code == 200


def test_cors_preflight_remains_available_behind_token_gate():
    app = FastAPI()

    @app.get("/")
    async def index():
        return {"status": "ok"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://studio.example.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessTokenMiddleware, token="correct-horse-battery-staple")
    response = _request(
        app,
        "OPTIONS",
        "/",
        {
            "Origin": "https://studio.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://studio.example.com"


def test_cors_parser_accepts_only_exact_http_origins():
    accepted, rejected = config._parse_cors_origins(
        "https://studio.example.com/, http://localhost:3000, *, file://local, https://bad.test/path"
    )
    assert accepted == ("https://studio.example.com", "http://localhost:3000")
    assert rejected == ("*", "file://local", "https://bad.test/path")


def test_web_configuration_report_never_contains_the_token(monkeypatch):
    monkeypatch.setattr(config, "WEB_ACCESS_TOKEN", "a-secret-web-token")
    report = config.get_web_config_report()
    assert report["access_token_configured"] is True
    assert "a-secret-web-token" not in str(report)


def test_service_url_sanitizer_removes_embedded_credentials_and_query_tokens():
    value = "https://user:password@[2001:db8::1]:8443/v1?api_key=secret#fragment"
    assert config.sanitize_service_url(value) == "https://[2001:db8::1]:8443/v1"
    assert config.sanitize_service_url("not a URL") == "<invalid URL>"
