from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from http.cookies import SimpleCookie
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SignedSessionMiddleware:
    """Small signed-cookie session for non-sensitive owner and CSRF identifiers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        secret_key: str,
        session_cookie: str,
        max_age: int,
        same_site: str = "lax",
        https_only: bool = True,
    ) -> None:
        self.app = app
        self.secret = secret_key.encode("utf-8")
        self.session_cookie = session_cookie
        self.max_age = max_age
        self.same_site = same_site
        self.https_only = https_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        encoded = self._cookie_value(headers.get("cookie", ""))
        session = self._decode(encoded) if encoded else {}
        original = dict(session)
        scope["session"] = session

        async def send_with_session(message: Message) -> None:
            if message["type"] == "http.response.start" and session != original:
                cookie_header = self._set_cookie_header(session)
                message.setdefault("headers", []).append(
                    (b"set-cookie", cookie_header.encode("latin-1"))
                )
            await send(message)

        await self.app(scope, receive, send_with_session)

    def _cookie_value(self, raw_cookie: str) -> str | None:
        try:
            cookies = SimpleCookie()
            cookies.load(raw_cookie)
            morsel = cookies.get(self.session_cookie)
            return morsel.value if morsel else None
        except Exception:
            return None

    def _decode(self, encoded: str) -> dict[str, Any]:
        try:
            payload_part, signature_part = encoded.split(".", 1)
            supplied_signature = _b64decode(signature_part)
            expected_signature = hmac.new(
                self.secret,
                payload_part.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return {}
            payload = json.loads(_b64decode(payload_part))
            if not isinstance(payload, dict) or payload.get("expires", 0) < time.time():
                return {}
            data = payload.get("data")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _set_cookie_header(self, session: dict[str, Any]) -> str:
        cookie = SimpleCookie()
        if session:
            expires = int(time.time()) + self.max_age
            payload = _b64encode(
                json.dumps(
                    {"data": session, "expires": expires},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            signature = _b64encode(
                hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).digest()
            )
            cookie[self.session_cookie] = f"{payload}.{signature}"
            cookie[self.session_cookie]["max-age"] = str(self.max_age)
        else:
            cookie[self.session_cookie] = ""
            cookie[self.session_cookie]["max-age"] = "0"
        cookie[self.session_cookie]["path"] = "/"
        cookie[self.session_cookie]["httponly"] = True
        cookie[self.session_cookie]["samesite"] = self.same_site
        if self.https_only:
            cookie[self.session_cookie]["secure"] = True
        return cookie.output(header="").strip()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
