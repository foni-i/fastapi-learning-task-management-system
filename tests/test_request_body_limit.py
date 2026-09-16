"""Direct ASGI tests for the pre-multipart upload body boundary."""

import asyncio
import json
from collections.abc import Awaitable
from typing import cast

import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE
from app.middleware.request_body_limit import (
    KNOWLEDGE_UPLOAD_PATH,
    MULTIPART_ENVELOPE_BYTES,
    RequestBodyLimitExceeded,
    UploadBodyLimitMiddleware,
)
from app.services.knowledge_documents import MAX_DOCUMENT_BYTES


def _scope(
    *,
    path: str = KNOWLEDGE_UPLOAD_PATH,
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Scope:
    return cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "http",
            "method": method,
            "root_path": "",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers or [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
    )


def _run(value: Awaitable[None]) -> None:
    asyncio.run(value)


def _receive_messages(messages: list[Message]) -> tuple[Receive, list[Message]]:
    pending = list(messages)
    delivered: list[Message] = []

    async def receive() -> Message:
        message = pending.pop(0)
        delivered.append(message)
        return message

    return receive, delivered


def _recording_app(
    received_bodies: list[bytes],
    calls: list[Scope],
) -> ASGIApp:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope)
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            received_bodies.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


def _send_recorder(messages: list[Message]) -> Send:
    async def send(message: Message) -> None:
        messages.append(message)

    return send


def _assert_safe_413(sent: list[Message]) -> None:
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert json.loads(body) == {"detail": KNOWLEDGE_DOCUMENT_TOO_LARGE_MESSAGE}


def test_trusted_oversized_content_length_rejects_before_receive_or_app() -> None:
    calls: list[Scope] = []
    receive_calls = 0
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"", "more_body": False}

    limiter = UploadBodyLimitMiddleware(
        _recording_app([], calls),
        max_body_bytes=10,
    )
    _run(
        limiter(
            _scope(headers=[(b"content-length", b"11")]),
            receive,
            _send_recorder(sent),
        )
    )

    assert calls == []
    assert receive_calls == 0
    _assert_safe_413(sent)


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-length", b"invalid")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"1"), (b"content-length", b"2")],
        [(b"content-length", b"1")],
    ],
    ids=("missing", "invalid", "negative", "conflicting", "underreported"),
)
def test_actual_chunk_count_rejects_untrusted_or_underreported_length(
    headers: list[tuple[bytes, bytes]],
) -> None:
    received_bodies: list[bytes] = []
    sent: list[Message] = []
    receive, delivered = _receive_messages(
        [
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"78901", "more_body": False},
        ]
    )
    limiter = UploadBodyLimitMiddleware(
        _recording_app(received_bodies, []),
        max_body_bytes=10,
    )

    _run(limiter(_scope(headers=headers), receive, _send_recorder(sent)))

    assert len(delivered) == 2
    assert received_bodies == [b"123456"]
    _assert_safe_413(sent)


def test_exact_limit_and_non_target_routes_are_transparent() -> None:
    for scope in (_scope(), _scope(path="/health/live"), _scope(method="GET")):
        received_bodies: list[bytes] = []
        sent: list[Message] = []
        receive, _delivered = _receive_messages(
            [{"type": "http.request", "body": b"1234567890", "more_body": False}]
        )
        limiter = UploadBodyLimitMiddleware(
            _recording_app(received_bodies, []),
            max_body_bytes=10 if scope["path"] == KNOWLEDGE_UPLOAD_PATH else 1,
        )

        _run(limiter(scope, receive, _send_recorder(sent)))

        assert received_bodies == [b"1234567890"]
        assert [message["status"] for message in sent if "status" in message] == [204]


def test_maximum_legal_file_and_metadata_fit_fixed_envelope_budget() -> None:
    boundary = "b" * 70
    filename = "x" * 251 + ".txt"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    body = prefix + (b"x" * MAX_DOCUMENT_BYTES) + suffix
    total_limit = MAX_DOCUMENT_BYTES + MULTIPART_ENVELOPE_BYTES
    assert len(body) - MAX_DOCUMENT_BYTES < MULTIPART_ENVELOPE_BYTES
    assert len(body) <= total_limit

    total_received = 0
    sent: list[Message] = []

    async def app(_scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal total_received
        message = await receive()
        total_received += len(message.get("body", b""))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    receive, _delivered = _receive_messages(
        [{"type": "http.request", "body": body, "more_body": False}]
    )
    limiter = UploadBodyLimitMiddleware(app, max_body_bytes=total_limit)

    _run(limiter(_scope(), receive, _send_recorder(sent)))

    assert total_received == len(body)
    assert [message["status"] for message in sent if "status" in message] == [204]


def test_late_overflow_after_response_start_raises_without_second_response() -> None:
    sent: list[Message] = []
    receive, _delivered = _receive_messages(
        [{"type": "http.request", "body": b"too large", "more_body": False}]
    )

    async def early_response_app(
        _scope: Scope,
        app_receive: Receive,
        send: Send,
    ) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await app_receive()

    limiter = UploadBodyLimitMiddleware(early_response_app, max_body_bytes=1)

    with pytest.raises(RequestBodyLimitExceeded) as exc_info:
        _run(limiter(_scope(), receive, _send_recorder(sent)))

    assert str(exc_info.value) == ""
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
