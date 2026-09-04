"""Optional access-token gate for the Web Studio."""
from __future__ import annotations

import base64
import binascii
import hmac
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def _basic_password(value: str) -> str | None:
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    _username, password = decoded.split(":", 1)
    return password


def request_access_token(headers: Headers) -> str | None:
    """Read Bearer, Basic-password, or dedicated-header credentials."""
    dedicated = headers.get("x-novel-workspace-token")
    if dedicated:
        return dedicated

    scheme, _, credential = headers.get("authorization", "").partition(" ")
    if not credential:
        return None
    if scheme.lower() == "bearer":
        return credential
    if scheme.lower() == "basic":
        return _basic_password(credential)
    return None


class AccessTokenMiddleware:
    """Protect Web routes when ``NOVEL_WEB_ACCESS_TOKEN`` is configured.

    Basic authentication keeps the browser UI usable without injecting a
    secret into JavaScript. Bearer and ``X-Novel-Workspace-Token`` are also
    accepted for API clients. Health probes and CORS preflight stay public.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str = "",
        public_paths: Iterable[str] = ("/healthz", "/readyz"),
    ) -> None:
        self.app = app
        self.token = token
        self.public_paths = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not self.token
            or scope.get("method") == "OPTIONS"
            or scope.get("path") in self.public_paths
        ):
            await self.app(scope, receive, send)
            return

        supplied = request_access_token(Headers(scope=scope))
        if supplied is not None and hmac.compare_digest(supplied, self.token):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            {"detail": "Web Studio authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Novel Workspace", charset="UTF-8"'},
        )
        await response(scope, receive, send)
