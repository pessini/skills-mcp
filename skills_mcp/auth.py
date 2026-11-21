"""Check a shared bearer token before handling MCP requests."""

import os
import re
from hmac import compare_digest

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


def bearer_token_from_env() -> bytes | None:
    token = os.environ.get("MCP_BEARER_TOKEN")
    if token is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._~+/\-]+=*", token):
        raise ValueError("MCP_BEARER_TOKEN must be a non-empty bearer token")
    return token.encode("ascii")


class RequireBearerToken:
    def __init__(self, app: ASGIApp, token: bytes) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            values = Headers(scope=scope).getlist("authorization")
            if len(values) == 1:
                scheme, _, candidate = values[0].encode("latin-1").partition(b" ")
                if scheme.lower() == b"bearer" and compare_digest(
                    candidate.lstrip(b" "), self.token
                ):
                    await self.app(scope, receive, send)
                    return
            await Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
