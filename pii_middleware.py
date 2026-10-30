"""Pure-ASGI PII Redaction Middleware for Rowing Coach.

Intercepts incoming user messages at the HTTP boundary before prompts reach
ADK, the LLM, Vertex AI durable sessions, or Memory Bank.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable

from rowing_coach import guardrails

log = logging.getLogger(__name__)


class RedactUserMessages:
    """Pure-ASGI middleware: DLP-redacts user messages before execution.

    Fails closed: if Cloud DLP is unavailable or errors, refuses the request with 503.
    """

    def __init__(self, app, paths: tuple[str, ...] = ("/api/adk", "/api/stream_reasoning_engine", "/stream_reasoning_engine")):
        self.app = app
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in self.paths
        ):
            return await self.app(scope, receive, send)

        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break

        try:
            data = json.loads(body)
            # Handle AG-UI / CopilotKit payload shape: {"messages": [...]}
            if "messages" in data and isinstance(data["messages"], list):
                for m in data["messages"]:
                    if m.get("role") != "user":
                        continue
                    c = m.get("content")
                    if isinstance(c, str):
                        m["content"] = guardrails.redact_boundary(c)
                    elif isinstance(c, list):
                        for part in c:
                            if isinstance(part, dict) and isinstance(part.get("text"), str):
                                part["text"] = guardrails.redact_boundary(part["text"])

            # Handle Reasoning Engine / Playground payload shape: {"input": {"message": ...}} or {"message": ...}
            elif "input" in data and isinstance(data["input"], dict):
                inp = data["input"]
                for key in ("message", "query", "prompt"):
                    val = inp.get(key)
                    if isinstance(val, str):
                        inp[key] = guardrails.redact_boundary(val)
                    elif isinstance(val, dict) and "text" in val:
                        val["text"] = guardrails.redact_boundary(val["text"])

            for top_key in ("message", "query", "prompt"):
                val = data.get(top_key)
                if isinstance(val, str):
                    data[top_key] = guardrails.redact_boundary(val)

            body = json.dumps(data).encode("utf-8")
        except json.JSONDecodeError:
            pass  # not JSON: let downstream reject
        except Exception:
            log.exception("PII redaction failed; refusing request (fail closed)")
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"error":"PII redaction is unavailable, so the request was refused."}',
            })
            return

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode("utf-8")))
        await self.app({**scope, "headers": headers}, replay, send)
