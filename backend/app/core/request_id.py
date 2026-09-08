from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def get_request_id() -> str | None:
    return request_id_context.get()


class RequestIdMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode("utf-8").strip() or new_request_id()
        token = request_id_context.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("utf-8")))
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_context.reset(token)
