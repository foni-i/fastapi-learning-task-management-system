"""Stream-limit the knowledge upload body before multipart parsing."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE

KNOWLEDGE_UPLOAD_PATH = "/api/v1/knowledge/documents"
MULTIPART_ENVELOPE_BYTES = 64 * 1024


class RequestBodyLimitExceeded(Exception):
    """Stop a request stream without carrying input or size details."""


def _trusted_content_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    """Return one valid nonnegative Content-Length or require stream counting."""

    values = [
        value.strip() for name, value in headers if name.lower() == b"content-length"
    ]
    if len(values) != 1:
        return None
    try:
        length = int(values[0])
    except ValueError:
        return None
    return length if length >= 0 else None


class UploadBodyLimitMiddleware:
    """Limit only the document-upload route by actual ASGI body bytes."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        path: str = KNOWLEDGE_UPLOAD_PATH,
    ) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope.get("method", "").upper() == "POST"
            and scope.get("path") == self.path
        ):
            await self.app(scope, receive, send)
            return

        content_length = _trusted_content_length(scope.get("headers", []))
        if content_length is not None and content_length > self.max_body_bytes:
            await self._send_too_large(scope, receive, send)
            return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise RequestBodyLimitExceeded
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyLimitExceeded:
            if response_started:
                raise
            await self._send_too_large(scope, receive, send)

    @staticmethod
    async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE},
        )
        await response(scope, receive, send)
