"""Unit tests for pure-ASGI RedactUserMessages middleware in Rowing Coach."""

import json
from unittest.mock import patch
import pytest

from pii_middleware import RedactUserMessages


@pytest.mark.anyio
async def test_pii_middleware_redacts_user_message():
    """Verify that user messages are scrubbed through guardrails.redact_boundary."""
    received_body = None

    async def mock_app(scope, receive, send):
        nonlocal received_body
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        received_body = body
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"status":"ok"}'})

    middleware = RedactUserMessages(mock_app, paths=("/api/adk",))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/adk",
        "headers": [(b"content-type", b"application/json")],
    }

    raw_payload = {
        "messages": [
            {"role": "user", "content": "Update athlete Daniel Okonkwo at 06 12 34 56 78"}
        ]
    }
    body_bytes = json.dumps(raw_payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    sent_messages = []

    async def send(msg):
        sent_messages.append(msg)

    with patch("rowing_coach.guardrails.redact_boundary", side_effect=lambda t: t.replace("Daniel Okonkwo", "[PERSON_NAME]").replace("06 12 34 56 78", "[PHONE_NUMBER]")):
        await middleware(scope, receive, send)

    assert received_body is not None
    data = json.loads(received_body.decode("utf-8"))
    assert "[PERSON_NAME]" in data["messages"][0]["content"]
    assert "[PHONE_NUMBER]" in data["messages"][0]["content"]
    assert "Daniel Okonkwo" not in data["messages"][0]["content"]


@pytest.mark.anyio
async def test_pii_middleware_fails_closed_on_error():
    """Verify that middleware returns 503 if DLP/redaction raises an exception."""
    async def mock_app(scope, receive, send):
        pass

    middleware = RedactUserMessages(mock_app, paths=("/api/adk",))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/adk",
        "headers": [(b"content-type", b"application/json")],
    }

    raw_payload = {"messages": [{"role": "user", "content": "Hello coach"}]}
    body_bytes = json.dumps(raw_payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    sent_messages = []

    async def send(msg):
        sent_messages.append(msg)

    with patch("rowing_coach.guardrails.redact_boundary", side_effect=RuntimeError("Cloud DLP unavailable")):
        await middleware(scope, receive, send)

    # Assert 503 status
    start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
    assert start_msg["status"] == 503
    body_msg = next(m for m in sent_messages if m["type"] == "http.response.body")
    assert b"PII redaction is unavailable" in body_msg["body"]
