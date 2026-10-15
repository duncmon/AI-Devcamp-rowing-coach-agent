"""Agent Runtime entrypoint for Rowing Coach ADK agent.

Exposes the reasoning engine streaming contract (/api/stream_reasoning_engine and /stream_reasoning_engine)
and health check (/healthz). Configures VertexAiSessionService and VertexAiMemoryBankService
when APP_URL is injected by Agent Runtime.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.memory import InMemoryMemoryService, VertexAiMemoryBankService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, VertexAiSessionService
from google.genai import types

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

if os.environ.get("APP_URL"):
    os.environ.setdefault("GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES", "false")

from rowing_coach.agent import root_agent  # noqa: E402

app = FastAPI(title="rowing-coach-agent runtime backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_APP_URL_FULL_RE = re.compile(
    r"/projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)"
    r"/reasoningEngines/(?P<engine>\d+)"
)

app_url = os.environ.get("APP_URL", "").strip()
match = _APP_URL_FULL_RE.search(app_url) if app_url else None

if match:
    project = match.group("project")
    location = match.group("location")
    engine_id = match.group("engine")
    session_service = VertexAiSessionService(
        project=project,
        location=location,
        agent_engine_id=engine_id,
    )
    memory_service = VertexAiMemoryBankService(
        project=project,
        location=location,
        agent_engine_id=engine_id,
    )
    log.info("Initialized VertexAiSessionService and VertexAiMemoryBankService on engine %s", engine_id)
else:
    session_service = InMemorySessionService()
    memory_service = InMemoryMemoryService()
    log.info("Initialized InMemorySessionService and InMemoryMemoryService")

runner = Runner(
    app_name="rowing_coach",
    agent=root_agent,
    session_service=session_service,
    memory_service=memory_service,
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/api/stream_reasoning_engine")
@app.post("/stream_reasoning_engine")
async def stream_reasoning_engine(request: Request):
    try:
        payload = await request.json()
    except Exception:
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}
    log.info("stream_reasoning_engine payload: %s", payload)
    args = payload.get("input") or {}
    raw_message = (
        args.get("message")
        or args.get("query")
        or args.get("prompt")
        or args.get("content")
        or payload.get("message")
        or payload.get("query")
        or payload.get("prompt")
    )
    if not raw_message:
        log.error("No valid message field found in payload: %s", payload)
        return JSONResponse({"error": "input.message or input.query is required"}, status_code=400)

    if isinstance(raw_message, dict):
        try:
            content = types.Content.model_validate(raw_message)
        except Exception:
            text = raw_message.get("text") or str(raw_message)
            content = types.Content(role="user", parts=[types.Part(text=text)])
    elif isinstance(raw_message, str):
        content = types.Content(role="user", parts=[types.Part(text=raw_message)])
    else:
        content = types.Content(role="user", parts=[types.Part(text=str(raw_message))])

    raw_user_id = args.get("user_id") or payload.get("user_id")
    if not raw_user_id or str(raw_user_id).strip() in ("vais-query-reasoning-engine", "default", "anonymous", ""):
        user_id = "coach-david"
    else:
        user_id = str(raw_user_id).strip()
    log.info("Resolved user_id for memory bank and session service: %s", user_id)
    session_id = args.get("session_id") or payload.get("session_id")
    session = None
    if session_id:
        try:
            session = await runner.session_service.get_session(
                app_name="rowing_coach", user_id=user_id, session_id=session_id
            )
        except Exception:
            session = None
    if session is None:
        if session_id:
            try:
                session = await runner.session_service.create_session(
                    app_name="rowing_coach", user_id=user_id, session_id=str(session_id)
                )
            except Exception:
                session = await runner.session_service.create_session(app_name="rowing_coach", user_id=user_id)
        else:
            session = await runner.session_service.create_session(app_name="rowing_coach", user_id=user_id)

    cfg = RunConfig(streaming_mode=StreamingMode.SSE)

    async def lines():
        try:
            yield json.dumps({"session_id": session.id, "user_id": user_id}) + "\n"
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                run_config=cfg,
                new_message=content,
            ):
                yield json.dumps({"output": event.model_dump(mode="json", exclude_none=True)}) + "\n"
        except Exception as e:
            log.exception("stream_reasoning_engine failed")
            yield json.dumps({"error": str(e)[:300]}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")
