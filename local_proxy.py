"""Local Authenticated Proxy & Direct Chat Interface for Rowing Coach Agent Runtime.

Provides:
1. Terminal CLI with instant streaming (<2s latency, bypassing Cloud Console Playground buffering):
   python local_proxy.py cli "What is our session for today, Oct 15?"
   python local_proxy.py cli  (interactive multi-turn session)

2. Local FastAPI Web UI running on localhost (incorporating Lab 2 ThreadPersistence):
   python local_proxy.py serve --port 8000
   Open http://localhost:8000 in your browser for a responsive coaching chat interface.
"""

import argparse
import json
import os
import pathlib
import sys
import uuid
from typing import AsyncGenerator, Dict, Optional, Tuple

import google.auth
import google.auth.transport.requests
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
DEPLOYMENT_METADATA_FILE = PROJECT_ROOT / "deployment_metadata.json"


def resolve_engine_target() -> Tuple[str, str]:
    """Resolves the regional API endpoint and resource path for the deployed engine."""
    region = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
    engine_path = None

    if DEPLOYMENT_METADATA_FILE.exists():
        try:
            with open(DEPLOYMENT_METADATA_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
                engine_path = meta.get("remote_agent_runtime_id")
        except Exception:
            pass

    if not engine_path:
        project_num = os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        engine_id = os.environ.get("AGENT_ENGINE_ID")
        if not project_num or not engine_id:
            raise ValueError(
                "Could not resolve deployed engine target. "
                "Ensure deployment_metadata.json exists, or set GOOGLE_CLOUD_PROJECT and AGENT_ENGINE_ID in your environment."
            )
        engine_path = f"projects/{project_num}/locations/{region}/reasoningEngines/{engine_id}"

    # Extract location if present in path
    if "/locations/" in engine_path:
        parts = engine_path.split("/")
        loc_idx = parts.index("locations") + 1
        if loc_idx < len(parts):
            region = parts[loc_idx]

    api_url = f"https://{region}-aiplatform.googleapis.com/reasoningEngines/v1/{engine_path}/api/stream_reasoning_engine"
    return api_url, engine_path


def get_access_token() -> str:
    """Acquires a valid Google Cloud OAuth2 Bearer token from Application Default Credentials."""
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


async def stream_remote_agent(
    message: str,
    user_id: str = "coach-david",
    session_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Streams responses from the deployed Reasoning Engine instance with deduplication."""
    api_url, _ = resolve_engine_target()
    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": {
            "message": message,
            "user_id": user_id,
            "session_id": session_id,
        }
    }

    seen_calls = set()
    streamed_any_partial = False

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                yield f"\n[Error {resp.status_code}]: {body.decode('utf-8', errors='replace')}\n"
                return

            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    out = data.get("output")
                    if isinstance(out, dict):
                        is_partial = out.get("partial")
                        inv_id = out.get("id") or out.get("invocation_id")

                        parts = (
                            out.get("content", {}).get("parts", [])
                            or out.get("parts", [])
                        )
                        for p in parts:
                            if isinstance(p, dict) and "function_call" in p:
                                fn = p["function_call"]
                                fn_name = fn.get("name")
                                call_key = fn.get("id") or f"call_{inv_id}_{fn_name}"
                                if call_key not in seen_calls:
                                    seen_calls.add(call_key)
                                    yield f"\n[Tool Invocation: {fn_name}]\n"
                            elif isinstance(p, dict) and "text" in p:
                                txt = p["text"]
                                if not txt:
                                    continue
                                # Only stream incremental deltas from partial=True.
                                # Events with partial=False are full aggregates of previously streamed deltas.
                                if is_partial is True:
                                    streamed_any_partial = True
                                    yield txt
                                elif is_partial is False and not streamed_any_partial:
                                    yield txt
                    elif "error" in data:
                        yield f"\n[Agent Error]: {data['error']}\n"
                except Exception:
                    yield line + "\n"


# ------------------------------------------------------------------------------
# FastAPI Web App
# ------------------------------------------------------------------------------
app = FastAPI(title="Rowing Coach Local Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    api_url, engine_path = resolve_engine_target()
    return {"status": "ok", "target_engine": engine_path, "api_url": api_url}


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    data = await request.json()
    message = data.get("message", "")
    session_id = data.get("session_id")
    user_id = data.get("user_id", "coach-david")

    async def sse_stream():
        async for chunk in stream_remote_agent(message, user_id=user_id, session_id=session_id):
            payload = json.dumps({"text": chunk})
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>ACL Masters Rowing Coach — Agent Interface</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/water.css@2/out/water.css">
  <style>
    body { max-width: 900px; margin: auto; padding: 20px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    #chat-box { height: 60vh; overflow-y: auto; border: 1px solid #444; border-radius: 8px; padding: 15px; margin-bottom: 15px; background: #161b22; }
    .msg { margin-bottom: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
    .msg.user { color: #58a6ff; font-weight: 600; }
    .msg.agent { color: #e6edf3; }
    .msg.tool { color: #d29922; font-style: italic; font-size: 0.9em; }
    .banner { background: #161b22 !important; color: #f0f6fc !important; padding: 14px 18px !important; border-left: 4px solid #238636 !important; border-radius: 6px !important; margin-bottom: 15px !important; font-size: 0.95em !important; line-height: 1.6 !important; }
    .banner strong { color: #58a6ff !important; }
    .controls { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
    #input-form { display: flex; gap: 10px; }
    #input-text { flex-grow: 1; padding: 10px; border-radius: 6px; border: 1px solid #444; }
    button { padding: 10px 20px; cursor: pointer; border-radius: 6px; }
    .btn-secondary { background: #30363d; color: #c9d1d9; border: 1px solid #8b949e; padding: 5px 12px; font-size: 0.85em; }
  </style>
</head>
<body>
  <h2>🚣‍♂️ ACL Masters Rowing Coach Assistant</h2>
  <div class="banner">
    <strong>Active Context:</strong> Today is <strong>October 15, 2026</strong> (Week 6, Phase 1).<br>
    <strong>Squad Override:</strong> 4-Week Anaerobic Block (Weeks 5-8) active for Coach David Montgamery.<br>
    <strong>Connected Engine:</strong> Google Cloud Agent Runtime (Region: us-central1) via ADC proxy.
  </div>

  <div class="controls">
    <span id="session-label" style="font-size: 0.85em; color: #8b949e;"></span>
    <button type="button" class="btn-secondary" id="reset-session-btn">🔄 Start New Chat</button>
  </div>

  <div id="chat-box"></div>

  <form id="input-form">
    <input type="text" id="input-text" placeholder="Ask about squad athletes, today's workout, or coach David's splits..." autocomplete="off" required autofocus>
    <button type="submit" id="send-btn">Send</button>
  </form>

  <script>
    // Lab 2 ThreadPersistence pattern: persist session across reloads in localStorage
    const STORAGE_KEY = 'rowing-coach-session-thread';
    let currentSessionId = localStorage.getItem(STORAGE_KEY);
    if (!currentSessionId) {
      currentSessionId = 'session-' + Math.random().toString(36).substring(2, 10);
      localStorage.setItem(STORAGE_KEY, currentSessionId);
    }

    const sessionLabel = document.getElementById('session-label');
    sessionLabel.textContent = 'Durable Session: ' + currentSessionId;

    document.getElementById('reset-session-btn').addEventListener('click', () => {
      const newId = 'session-' + Math.random().toString(36).substring(2, 10);
      localStorage.setItem(STORAGE_KEY, newId);
      window.location.reload();
    });

    const chatBox = document.getElementById('chat-box');
    const form = document.getElementById('input-form');
    const input = document.getElementById('input-text');
    const sendBtn = document.getElementById('send-btn');

    function appendMessage(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      if (role === 'user') div.textContent = 'Coach: ' + text;
      else div.textContent = text;
      chatBox.appendChild(div);
      chatBox.scrollTop = chatBox.scrollHeight;
      return div;
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      sendBtn.disabled = true;

      appendMessage('user', text);
      const agentMsg = appendMessage('agent', '');

      try {
        const response = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, session_id: currentSessionId, user_id: 'coach-david' })
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\\n\\n');
          buffer = lines.pop();

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const payload = line.slice(6);
              if (payload === '[DONE]') break;
              try {
                const parsed = JSON.parse(payload);
                agentMsg.textContent += parsed.text;
                chatBox.scrollTop = chatBox.scrollHeight;
              } catch (err) {}
            }
          }
        }
      } catch (err) {
        agentMsg.textContent += '\\n[Network Error]: ' + err.message;
      } finally {
        sendBtn.disabled = false;
        input.focus();
      }
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTMLResponse(content=HTML_PAGE)


# ------------------------------------------------------------------------------
# CLI Runner
# ------------------------------------------------------------------------------
async def run_cli(initial_prompt: Optional[str] = None):
    print("\n=======================================================")
    print(" 🚣‍♂️ Rowing Coach Agent — Direct Terminal Interface")
    print("=======================================================")
    api_url, engine_path = resolve_engine_target()
    print(f"Engine Target : {engine_path}")
    print(f"Direct API    : {api_url}")
    print("Authentication: Application Default Credentials (ADC)")
    print("=======================================================\n")

    # Reuse persistent session ID for the terminal conversation
    session_id = f"cli-session-{uuid.uuid4().hex[:8]}"

    if initial_prompt:
        print(f"Coach: {initial_prompt}")
        print("Agent: ", end="", flush=True)
        async for chunk in stream_remote_agent(initial_prompt, session_id=session_id):
            print(chunk, end="", flush=True)
        print("\n")
        return

    print(f"[Active Multi-Turn Session: {session_id}] (Type 'exit' to quit)\n")
    while True:
        try:
            prompt = input("Coach > ").strip()
            if not prompt or prompt.lower() in ("exit", "quit"):
                break
            print("\nAgent: ", end="", flush=True)
            async for chunk in stream_remote_agent(prompt, session_id=session_id):
                print(chunk, end="", flush=True)
            print("\n")
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break


def main():
    parser = argparse.ArgumentParser(description="Rowing Coach Local Proxy & UI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # CLI subcommand
    cli_parser = subparsers.add_parser("cli", help="Run interactive terminal chat")
    cli_parser.add_argument("prompt", nargs="?", default=None, help="Optional one-shot prompt")

    # Serve subcommand
    serve_parser = subparsers.add_parser("serve", help="Run local web server and UI")
    serve_parser.add_argument("--port", type=int, default=8000, help="Local port (default 8000)")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default 127.0.0.1)")

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        print(f"Starting Rowing Coach Local Proxy UI on http://{args.host}:{args.port}")
        uvicorn.run("local_proxy:app", host=args.host, port=args.port, reload=False)
    else:
        # Default to CLI
        import asyncio
        initial_prompt = getattr(args, "prompt", None)
        asyncio.run(run_cli(initial_prompt))


if __name__ == "__main__":
    main()
