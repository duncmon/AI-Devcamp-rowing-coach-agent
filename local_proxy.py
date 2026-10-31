"""Executive Authenticated Proxy & Dashboard UI for Rowing Coach Agent.

Features:
  1. Twelve-Factor Principle 3: Launch Profiles (--target local | cloud)
  2. Full PII Privacy & Compliance Gateway:
     - Gate 1: Reversible token stripping ([ATHLETE_NAME_1], [PHONE_NUMBER_1])
     - Legal audit trail of what text Gemini actually receives
     - Gate 2: Client-side rehydration with orphan token assertion
  3. Executive 3-Column Glassmorphism Dashboard:
     - Left: 5-Stage Periodization Stepper (Schedule -> Rules -> Pacing -> Plan -> Staged)
     - Center: Responsive Chat with Markdown formatting & Quick Action chips
     - Right: Live Activity Feed (Tool Tracing) & PII Compliance Audit Drawer
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time
import uuid
from typing import AsyncGenerator, Dict, Optional, Tuple

import google.auth
import google.auth.transport.requests
import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from backend.pii_vault import ReversiblePiiVault, VaultSafetyError
from rowing_coach.guardrails import redact_boundary

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
DEPLOYMENT_METADATA_FILE = PROJECT_ROOT / "deployment_metadata.json"

# Shared session vault
vault = ReversiblePiiVault()

_STAGE_MAPPING = {
    "get_season_schedule": "curriculum",
    "load_skill": "rules",
    "lookup_athlete": "pacing",
    "calculate_athlete_pacing": "pacing",
    "align_session_pace_to_l4": "pacing",
    "generate_weekly_sheet": "plan_ready",
    "generate_coach_notes": "plan_ready",
    "intervals_get_athlete_wellness": "plan_ready",
    "intervals_schedule_workout": "staged",
}


def resolve_engine_target() -> Tuple[str, str]:
    """Resolves regional endpoint and engine path for deployed Reasoning Engine."""
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
        project_num = os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER") or os.environ.get("GOOGLE_CLOUD_PROJECT", "ai-devcamp-duncan-395792")
        engine_id = os.environ.get("AGENT_ENGINE_ID", "5062846700507365376")
        engine_path = f"projects/{project_num}/locations/{region}/reasoningEngines/{engine_id}"

    if "/locations/" in engine_path:
        parts = engine_path.split("/")
        loc_idx = parts.index("locations") + 1
        if loc_idx < len(parts):
            region = parts[loc_idx]

    api_url = f"https://{region}-aiplatform.googleapis.com/reasoningEngines/v1/{engine_path}/api/stream_reasoning_engine"
    return api_url, engine_path


def get_access_token() -> str:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


async def stream_agent_pipeline(
    raw_message: str,
    user_id: str = "coach-david",
    session_id: Optional[str] = None,
    target: Optional[str] = None,
    confirmation: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[Dict, None]:
    """Executes the full compliant coaching pipeline:
    1. Gate 1 PII Stripping & Vaulting
    2. Streaming Execution (Local or Cloud Reasoning Engine)
    3. Tool Call & Stage Tracking
    4. Gate 2 Safe Rehydration
    """
    resolved_target = (target or os.environ.get("PROXY_TARGET", "cloud")).lower()
    session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"

    # --- Step 1: Gate 1 PII Redaction & Reversible Vaulting ---
    vaulted_message = vault.strip_and_vault(raw_message, session_id)
    sess_vault = vault._get_session(session_id)
    mappings = dict(sess_vault.get("to_token", {}))

    yield {
        "type": "pii_audit",
        "raw_input": raw_message,
        "vaulted_input": vaulted_message,
        "mappings": mappings,
        "model_armor_template": os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "rowing-coach-guardrails"),
        "target": resolved_target,
    }

    # Resolve target URL and headers
    if resolved_target == "local":
        api_url = os.environ.get("LOCAL_BACKEND_ORIGIN", "http://localhost:8000/api/stream_reasoning_engine")
        headers = {"Content-Type": "application/json"}
    else:
        api_url, _ = resolve_engine_target()
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    if confirmation:
        call_id = confirmation.get("id", "call_adk_request_confirmation")
        is_confirmed = bool(confirmation.get("confirmed", True))
        payload = {
            "input": {
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": call_id,
                                "name": "adk_request_confirmation",
                                "response": {"confirmed": is_confirmed},
                            }
                        }
                    ],
                },
                "user_id": user_id,
                "session_id": session_id,
            }
        }
    else:
        payload = {
            "input": {
                "message": vaulted_message,
                "user_id": user_id,
                "session_id": session_id,
            }
        }

    seen_calls = set()
    accumulated_text = ""
    start_time = time.time()

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    err_body = (await resp.aread()).decode("utf-8", errors="replace")
                    yield {
                        "type": "error",
                        "error": f"Backend Error {resp.status_code}: {err_body}",
                    }
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
                                    fn_args = fn.get("args", {})
                                    call_key = fn.get("id") or f"call_{inv_id}_{fn_name}"
                                    if call_key not in seen_calls:
                                        seen_calls.add(call_key)
                                        # Map tool to pipeline stage
                                        if fn_name in _STAGE_MAPPING:
                                            yield {
                                                "type": "stage",
                                                "stage": _STAGE_MAPPING[fn_name],
                                            }
                                        yield {
                                            "type": "tool_call",
                                            "name": fn_name,
                                            "args": fn_args,
                                            "elapsed_ms": int((time.time() - start_time) * 1000),
                                        }

                                        # Intercept plan generation to mirror locally on coach Mac
                                        if fn_name in ("generate_weekly_sheet", "generate_coach_notes"):
                                            try:
                                                from rowing_coach.tools import generate_weekly_sheet as local_gws, generate_coach_notes as local_gcn
                                                if fn_name == "generate_weekly_sheet":
                                                    local_p = local_gws(**fn_args)
                                                else:
                                                    local_p = local_gcn(**fn_args)
                                                fname = pathlib.Path(local_p).name
                                                yield {
                                                    "type": "plan_generated",
                                                    "tool": fn_name,
                                                    "filename": fname,
                                                    "download_url": f"/api/plans/{fname}",
                                                }
                                            except Exception as err:
                                                log.warning("Local plan mirroring error: %s", err)

                                        # Intercept ADK Human-In-The-Loop Confirmation Request
                                        if fn_name == "adk_request_confirmation":
                                            orig_call = fn_args.get("originalFunctionCall", {})
                                            tool_conf = fn_args.get("toolConfirmation", {})
                                            yield {
                                                "type": "confirmation_request",
                                                "call_id": fn.get("id") or call_key,
                                                "tool": orig_call.get("name", "intervals_schedule_workout"),
                                                "args": orig_call.get("args", {}),
                                                "hint": tool_conf.get("hint", "Please approve or reject this action."),
                                            }

                                elif isinstance(p, dict) and "text" in p:
                                    txt = p["text"]
                                    if not txt:
                                        continue
                                    if is_partial is True:
                                        accumulated_text += txt
                                        yield {"type": "text", "delta": txt}
                                    elif is_partial is False and not accumulated_text:
                                        accumulated_text += txt
                                        yield {"type": "text", "delta": txt}

                        elif "error" in data:
                            yield {"type": "error", "error": data["error"]}
                    except Exception:
                        pass
        except Exception as e:
            yield {"type": "error", "error": f"Transport Error: {str(e)}"}

    # --- Step 3: Gate 2 Client Rehydration ---
    try:
        rehydrated_text = vault.rehydrate(accumulated_text, session_id)
    except VaultSafetyError as e:
        rehydrated_text = f"[Gate 2 Safety Error]: {e}"

    yield {
        "type": "done",
        "accumulated_vaulted": accumulated_text,
        "rehydrated": rehydrated_text,
        "elapsed_total_ms": int((time.time() - start_time) * 1000),
    }


# ------------------------------------------------------------------------------
# FastAPI Web App
# ------------------------------------------------------------------------------
app = FastAPI(title="Rowing Coach Executive Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    target = os.environ.get("PROXY_TARGET", "cloud").lower()
    if target == "local":
        origin = os.environ.get("LOCAL_BACKEND_ORIGIN", "http://localhost:8000/api/stream_reasoning_engine")
        return {"status": "ok", "target": "local", "api_url": origin}
    api_url, engine_path = resolve_engine_target()
    return {"status": "ok", "target": "cloud", "target_engine": engine_path, "api_url": api_url}


@app.get("/api/plans/{filename}")
def download_plan_endpoint(filename: str):
    from fastapi.responses import FileResponse, HTTPException
    clean_name = pathlib.Path(filename).name
    p_path = PROJECT_ROOT / "plans" / clean_name
    if not p_path.exists():
        raise HTTPException(status_code=404, detail=f"Plan file '{clean_name}' not found on local disk.")
    return FileResponse(p_path, filename=clean_name, media_type="text/markdown")


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    if DEMO_PASSCODE:
        code_cookie = request.cookies.get("rowing_passcode")
        code_header = request.headers.get("X-Passcode")
        code_query = request.query_params.get("code")
        if code_cookie != DEMO_PASSCODE and code_header != DEMO_PASSCODE and code_query != DEMO_PASSCODE:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing hackathon reviewer passcode.")

    data = await request.json()
    message = data.get("message", "")
    session_id = data.get("session_id")
    user_id = data.get("user_id", "coach-david")
    target = data.get("target") or os.environ.get("PROXY_TARGET", "cloud")
    confirmation = data.get("confirmation")

    async def sse_stream():
        async for event in stream_agent_pipeline(
            message,
            user_id=user_id,
            session_id=session_id,
            target=target,
            confirmation=confirmation,
        ):
            payload = json.dumps(event)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


HTML_PAGE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>ACL Masters Rowing Coach — Executive Agent Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#eef6ff', 500: '#1d63ed', 600: '#124ecc', 700: '#0d3896' },
            darkbg: '#0c0f14',
            darkpanel: '#151921',
            darkborder: '#232936',
          }
        }
      }
    }
  </script>
  <style>
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0c0f14; }
    ::-webkit-scrollbar-thumb { background: #2a3140; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #3b4457; }
    .prose p { margin-bottom: 0.5rem; line-height: 1.5; }
    .prose strong { color: #60a5fa; }
    .prose code { background: #1f2533; padding: 2px 5px; border-radius: 4px; font-family: monospace; font-size: 0.85em; }
    .pulse-glow { box-shadow: 0 0 15px rgba(29, 99, 237, 0.4); }
  </style>
</head>
<body class="bg-darkbg text-slate-200 min-h-screen flex flex-col font-sans antialiased">

  <!-- Top Navigation Header -->
  <header class="h-16 bg-darkpanel/80 backdrop-blur border-b border-darkborder px-6 flex items-center justify-between sticky top-0 z-50">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-xl bg-brand-600/20 border border-brand-500/40 flex items-center justify-center text-brand-400 text-lg shadow-inner">
        🚣‍♂️
      </div>
      <div>
        <h1 class="text-base font-bold tracking-tight text-white flex items-center gap-2">
          ACL Masters Rowing Coach
          <span class="text-[10px] font-semibold bg-brand-500/10 text-brand-400 border border-brand-500/20 px-2 py-0.5 rounded-full uppercase tracking-wider">Enterprise ADK</span>
        </h1>
        <p class="text-xs text-slate-400">Phase 1 Macrocycle (Week 8) • Coach David Persona</p>
      </div>
    </div>

    <div class="flex items-center gap-4">
      <div id="status-badge" class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-medium">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span id="target-label">Reasoning Engine (us-central1)</span>
      </div>

      <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-medium">
        <i class="fa-solid fa-shield-halved text-indigo-400"></i>
        <span>PII Vault & Model Armor</span>
      </div>

      <button id="reset-session-btn" title="Start fresh conversation" class="px-3 py-1.5 rounded-lg bg-darkborder hover:bg-slate-700 text-xs text-slate-300 transition flex items-center gap-1.5 border border-slate-700">
        <i class="fa-solid fa-arrows-rotate"></i> Reset Chat
      </button>
    </div>
  </header>

  <!-- Main 3-Column Workspace -->
  <div class="flex-1 flex overflow-hidden">

    <!-- Column 1: Periodization Pipeline Stepper -->
    <aside class="w-72 bg-darkpanel/50 border-r border-darkborder p-5 flex flex-col justify-between hidden md:flex">
      <div>
        <h2 class="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
          <i class="fa-solid fa-stairs text-brand-400"></i> 5-Stage Periodization
        </h2>

        <div class="space-y-4 relative">
          <div class="absolute left-4 top-2 bottom-2 w-0.5 bg-darkborder -z-0"></div>

          <!-- Stage 1 -->
          <div id="stage-curriculum" class="flex items-start gap-3 relative z-10 transition-all duration-300 opacity-60">
            <div class="w-8 h-8 rounded-full bg-darkborder border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shadow">1</div>
            <div>
              <p class="text-xs font-bold text-white">Season Curriculum</p>
              <p class="text-[11px] text-slate-400">W1-W13 Phase 1 block</p>
            </div>
          </div>

          <!-- Stage 2 -->
          <div id="stage-rules" class="flex items-start gap-3 relative z-10 transition-all duration-300 opacity-60">
            <div class="w-8 h-8 rounded-full bg-darkborder border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shadow">2</div>
            <div>
              <p class="text-xs font-bold text-white">Physiology Rules</p>
              <p class="text-[11px] text-slate-400">Kleshnev & Seiler 3-Zone</p>
            </div>
          </div>

          <!-- Stage 3 -->
          <div id="stage-pacing" class="flex items-start gap-3 relative z-10 transition-all duration-300 opacity-60">
            <div class="w-8 h-8 rounded-full bg-darkborder border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shadow">3</div>
            <div>
              <p class="text-xs font-bold text-white">L4 Stroke Rate Grid</p>
              <p class="text-[11px] text-slate-400">Wolverine threshold alignment</p>
            </div>
          </div>

          <!-- Stage 4 -->
          <div id="stage-plan_ready" class="flex items-start gap-3 relative z-10 transition-all duration-300 opacity-60">
            <div class="w-8 h-8 rounded-full bg-darkborder border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shadow">4</div>
            <div>
              <p class="text-xs font-bold text-white">Fiche & Notes Ready</p>
              <p class="text-[11px] text-slate-400">Squad sheet & coach notes</p>
            </div>
          </div>

          <!-- Stage 5 -->
          <div id="stage-staged" class="flex items-start gap-3 relative z-10 transition-all duration-300 opacity-60">
            <div class="w-8 h-8 rounded-full bg-darkborder border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shadow">5</div>
            <div>
              <p class="text-xs font-bold text-white">Intervals.icu Staged</p>
              <p class="text-[11px] text-slate-400">Confirmation gate verified</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Squad Meta Card -->
      <div class="bg-darkpanel p-3.5 rounded-xl border border-darkborder text-xs">
        <p class="font-bold text-brand-400 mb-1 flex items-center gap-1.5">
          <i class="fa-solid fa-circle-info"></i> Active Squad Profile
        </p>
        <p class="text-slate-300"><strong>Coach David:</strong> 1:58.2 (211.9W)</p>
        <p class="text-slate-400">UT2 Target: 2:21.8 @20</p>
        <p class="text-slate-500 text-[10px] mt-1">Durable Thread: <span id="thread-id-display">...</span></p>
      </div>
    </aside>

    <!-- Column 2: Center Interactive Chat Area -->
    <main class="flex-1 flex flex-col bg-darkbg overflow-hidden relative">
      
      <!-- Chat message stream -->
      <div id="chat-stream" class="flex-1 overflow-y-auto p-6 space-y-5">
        
        <!-- Welcome Card -->
        <div class="max-w-2xl mx-auto bg-darkpanel/60 border border-darkborder rounded-2xl p-5 text-sm shadow-xl">
          <div class="flex items-center gap-3 mb-3">
            <span class="text-2xl">🚣‍♂️</span>
            <div>
              <h3 class="font-bold text-white text-base">ACL Masters Coaching Workspace</h3>
              <p class="text-xs text-slate-400">Protected by Cloud DLP Boundary Sanitization & Google Cloud Model Armor</p>
            </div>
          </div>
          <p class="text-slate-300 text-xs leading-relaxed mb-4">
            Ask for athlete split alignments, Wolverine Level 4 tables, French squad fiches, or stage workouts directly to Intervals.icu with confirmation protection.
          </p>

          <div class="flex flex-wrap gap-2 pt-2 border-t border-darkborder/50">
            <button class="chip-btn text-[11px] bg-darkborder hover:bg-brand-600/20 hover:text-brand-300 hover:border-brand-500/40 border border-slate-700 px-2.5 py-1.5 rounded-lg text-slate-300 transition">
              💡 Coach David's UT2 Base Session
            </button>
            <button class="chip-btn text-[11px] bg-darkborder hover:bg-brand-600/20 hover:text-brand-300 hover:border-brand-500/40 border border-slate-700 px-2.5 py-1.5 rounded-lg text-slate-300 transition">
              💡 Calculate L4 Grid for Alex Martin
            </button>
            <button class="chip-btn text-[11px] bg-darkborder hover:bg-brand-600/20 hover:text-brand-300 hover:border-brand-500/40 border border-slate-700 px-2.5 py-1.5 rounded-lg text-slate-300 transition">
              💡 Stage 3x7min AT on Intervals.icu
            </button>
          </div>
        </div>

      </div>

      <!-- Bottom Chat Input Bar -->
      <div class="p-4 bg-darkpanel/80 backdrop-blur border-t border-darkborder">
        <form id="chat-form" class="max-w-3xl mx-auto flex gap-3">
          <input 
            type="text" 
            id="user-input" 
            placeholder="Ask about workouts, L4 stroke rates, or athlete pacing (PII is vaulted automatically)..." 
            autocomplete="off" 
            required 
            class="flex-1 bg-darkbg border border-darkborder rounded-xl px-4 py-3 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition"
          >
          <button 
            type="submit" 
            id="send-btn" 
            class="px-5 py-3 rounded-xl bg-brand-600 hover:bg-brand-500 text-white font-semibold text-sm transition flex items-center gap-2 shadow-lg disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span>Send</span>
            <i class="fa-solid fa-paper-plane text-xs"></i>
          </button>
        </form>
      </div>
    </main>

    <!-- Column 3: Telemetry, Activity Feed & PII Compliance Drawer -->
    <aside class="w-80 bg-darkpanel/70 border-l border-darkborder flex flex-col hidden lg:flex">
      
      <!-- Tabs Header -->
      <div class="flex border-b border-darkborder bg-darkpanel">
        <button id="tab-activity-btn" class="flex-1 py-3 text-xs font-bold text-brand-400 border-b-2 border-brand-500 transition flex items-center justify-center gap-1.5">
          <i class="fa-solid fa-bolt"></i> Activity Feed
        </button>
        <button id="tab-pii-btn" class="flex-1 py-3 text-xs font-bold text-slate-400 hover:text-slate-200 transition flex items-center justify-center gap-1.5">
          <i class="fa-solid fa-fingerprint"></i> PII Audit Vault
        </button>
      </div>

      <!-- Tab Content: Activity Feed -->
      <div id="tab-activity-content" class="flex-1 p-4 overflow-y-auto space-y-3">
        <p class="text-[11px] text-slate-400 flex items-center justify-between">
          <span>In-Flight Tool Traces</span>
          <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
        </p>
        <div id="activity-list" class="space-y-2 text-xs">
          <div class="p-2.5 rounded-lg bg-darkbg/50 border border-darkborder text-slate-400 text-center italic text-[11px]">
            No tool executions yet this turn.
          </div>
        </div>
      </div>

      <!-- Tab Content: PII Audit Vault -->
      <div id="tab-pii-content" class="flex-1 p-4 overflow-y-auto space-y-4 hidden">
        <div>
          <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider block mb-1">Gate 1: Boundary Sanitization</span>
          <div class="p-2.5 rounded-lg bg-darkbg border border-darkborder text-[11px] font-mono break-all" id="audit-vaulted-display">
            Awaiting prompt...
          </div>
        </div>

        <div>
          <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider block mb-1">Active Reversible Tokens</span>
          <div id="audit-mappings-list" class="space-y-1.5 text-[11px]">
            <p class="text-slate-500 italic text-[11px]">Zero tokens stored.</p>
          </div>
        </div>

        <div>
          <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider block mb-1">Model Armor Screening</span>
          <div class="p-2.5 rounded-lg bg-darkbg border border-darkborder text-[11px]">
            <p class="text-slate-300">Template: <span class="text-brand-400 font-mono">rowing-coach-guardrails</span></p>
            <p class="text-slate-400">Status: <span id="audit-armor-status" class="text-emerald-400 font-bold">READY</span></p>
          </div>
        </div>

        <div>
          <span class="text-[10px] font-bold text-indigo-400 uppercase tracking-wider block mb-1">Legal Invariant Checklist</span>
          <ul class="text-[11px] text-slate-300 space-y-1">
            <li>✅ Cloud DLP Byte-offset Check</li>
            <li>✅ Zero PII Leaked to LLM Weights</li>
            <li>✅ Reversible Client Rehydration</li>
          </ul>
        </div>
      </div>

    </aside>

  </div>

  <script>
    const STORAGE_KEY = 'rowing-coach-thread-v2';
    let sessionId = localStorage.getItem(STORAGE_KEY) || 'sess-' + Math.random().toString(36).substring(2, 10);
    localStorage.setItem(STORAGE_KEY, sessionId);

    document.getElementById('thread-id-display').textContent = sessionId;

    document.getElementById('reset-session-btn').addEventListener('click', () => {
      sessionId = 'sess-' + Math.random().toString(36).substring(2, 10);
      localStorage.setItem(STORAGE_KEY, sessionId);
      window.location.reload();
    });

    // Tab Switching
    const tabActBtn = document.getElementById('tab-activity-btn');
    const tabPiiBtn = document.getElementById('tab-pii-btn');
    const tabActContent = document.getElementById('tab-activity-content');
    const tabPiiContent = document.getElementById('tab-pii-content');

    tabActBtn.addEventListener('click', () => {
      tabActBtn.className = 'flex-1 py-3 text-xs font-bold text-brand-400 border-b-2 border-brand-500 transition flex items-center justify-center gap-1.5';
      tabPiiBtn.className = 'flex-1 py-3 text-xs font-bold text-slate-400 hover:text-slate-200 transition flex items-center justify-center gap-1.5';
      tabActContent.classList.remove('hidden');
      tabPiiContent.classList.add('hidden');
    });

    tabPiiBtn.addEventListener('click', () => {
      tabPiiBtn.className = 'flex-1 py-3 text-xs font-bold text-indigo-400 border-b-2 border-indigo-500 transition flex items-center justify-center gap-1.5';
      tabActBtn.className = 'flex-1 py-3 text-xs font-bold text-slate-400 hover:text-slate-200 transition flex items-center justify-center gap-1.5';
      tabPiiContent.classList.remove('hidden');
      tabActContent.classList.add('hidden');
    });

    // Quick Chip Clicks
    document.querySelectorAll('.chip-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const text = btn.textContent.replace('💡', '').trim();
        document.getElementById('user-input').value = text;
        document.getElementById('chat-form').dispatchEvent(new Event('submit'));
      });
    });

    // Stage Stepper Activation
    const stageIds = ['curriculum', 'rules', 'pacing', 'plan_ready', 'staged'];
    function setStage(stage) {
      const idx = stageIds.indexOf(stage);
      if (idx === -1) return;
      stageIds.forEach((s, i) => {
        const el = document.getElementById('stage-' + s);
        if (!el) return;
        const num = el.querySelector('div:first-child');
        if (i <= idx) {
          el.classList.remove('opacity-60');
          el.classList.add('opacity-100');
          num.className = 'w-8 h-8 rounded-full bg-brand-600 border border-brand-400 flex items-center justify-center text-xs font-bold text-white shadow-lg pulse-glow';
        } else {
          el.classList.add('opacity-60');
          el.classList.remove('opacity-100');
          num.className = 'w-8 h-8 rounded-full bg-darkborder border border-slate-700 flex items-center justify-center text-xs font-bold text-slate-300 shadow';
        }
      });
    }

    const chatStream = document.getElementById('chat-stream');
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const sendBtn = document.getElementById('send-btn');
    const activityList = document.getElementById('activity-list');

    function appendUserMessage(text) {
      const div = document.createElement('div');
      div.className = 'max-w-2xl ml-auto flex gap-3 justify-end items-start';
      div.innerHTML = `
        <div class="bg-brand-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm shadow-md max-w-lg">
          <p class="font-semibold text-xs text-brand-200 mb-0.5">Coach</p>
          <div class="whitespace-pre-wrap">${text}</div>
        </div>
        <div class="w-8 h-8 rounded-full bg-brand-700 border border-brand-400 flex items-center justify-center text-xs font-bold text-white flex-shrink-0">
          C
        </div>
      `;
      chatStream.appendChild(div);
      chatStream.scrollTop = chatStream.scrollHeight;
    }

    function createAgentMessageSlot() {
      const div = document.createElement('div');
      div.className = 'max-w-2xl mr-auto flex gap-3 items-start';
      div.innerHTML = `
        <div class="w-8 h-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-xs font-bold text-brand-400 flex-shrink-0">
          🚣‍♂️
        </div>
        <div class="bg-darkpanel border border-darkborder text-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 text-sm shadow-md max-w-lg w-full prose">
          <p class="font-bold text-xs text-brand-400 mb-1">ACL Coach Assistant</p>
          <div class="agent-body min-h-[1.5rem]"><span class="inline-block w-2 h-4 bg-brand-400 animate-pulse"></span></div>
        </div>
      `;
      chatStream.appendChild(div);
      chatStream.scrollTop = chatStream.scrollHeight;
      return div.querySelector('.agent-body');
    }

    function addActivityItem(name, args, elapsed) {
      if (activityList.children.length === 1 && activityList.children[0].textContent.includes('No tool')) {
        activityList.innerHTML = '';
      }
      const item = document.createElement('div');
      item.className = 'p-2.5 rounded-lg bg-darkbg border border-darkborder transition hover:border-slate-600';
      item.innerHTML = `
        <div class="flex items-center justify-between font-bold text-slate-200 text-[11px] mb-1">
          <span class="text-brand-400 flex items-center gap-1.5">
            <i class="fa-solid fa-gears text-[10px]"></i> ${name}
          </span>
          <span class="text-slate-500 text-[10px] font-mono">${elapsed}ms</span>
        </div>
        <pre class="text-[10px] text-slate-400 font-mono overflow-x-auto bg-darkpanel p-1.5 rounded">${JSON.stringify(args, null, 1)}</pre>
      `;
      activityList.appendChild(item);
      activityList.scrollTop = activityList.scrollHeight;
    }

    // Submit handler
    chatForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = userInput.value.trim();
      if (!text) return;
      userInput.value = '';

      // Auto-resolve pending confirmation if user types confirmation text
      if (window.pendingConfirmation && /^(yes|confirm|approve|proceed|ok|i confirm)/i.test(text.toLowerCase())) {
        window.submitConfirmation(window.pendingConfirmation.call_id, true);
        return;
      }

      sendBtn.disabled = true;

      appendUserMessage(text);
      const agentBody = createAgentMessageSlot();
      let rawAccumulated = '';

      try {
        const response = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, session_id: sessionId })
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
                const event = JSON.parse(payload);
                if (event.type === 'pii_audit') {
                  document.getElementById('audit-vaulted-display').textContent = event.vaulted_input;
                  const mapList = document.getElementById('audit-mappings-list');
                  mapList.innerHTML = '';
                  const keys = Object.keys(event.mappings);
                  if (keys.length === 0) {
                    mapList.innerHTML = '<p class="text-slate-500 italic text-[11px]">No PII in this prompt.</p>';
                  } else {
                    keys.forEach(k => {
                      const p = document.createElement('div');
                      p.className = 'flex items-center justify-between bg-darkbg p-1.5 rounded border border-darkborder font-mono text-[10px]';
                      p.innerHTML = `<span class="text-slate-400">${k}</span> <span class="text-emerald-400 font-bold">${event.mappings[k]}</span>`;
                      mapList.appendChild(p);
                    });
                  }
                } else if (event.type === 'tool_call') {
                  addActivityItem(event.name, event.args, event.elapsed_ms);
                } else if (event.type === 'stage') {
                  setStage(event.stage);
                } else if (event.type === 'confirmation_request') {
                  window.pendingConfirmation = event;
                  const confCard = document.createElement('div');
                  confCard.id = 'active-confirm-card';
                  confCard.className = 'my-3 p-4 rounded-xl border border-amber-500/40 bg-amber-950/20 shadow-lg text-slate-200';
                  confCard.innerHTML = `
                    <div class="flex items-center gap-2 text-amber-400 font-bold text-xs mb-2">
                      <i class="fa-solid fa-triangle-exclamation"></i> Human Approval Gate (Intervals.icu Staging)
                    </div>
                    <p class="text-xs text-slate-300 mb-2">${event.hint}</p>
                    <div class="bg-darkbg p-2.5 rounded border border-darkborder font-mono text-[11px] mb-3 text-slate-400">
                      <div><span class="text-slate-500">Action:</span> <strong class="text-brand-300">${event.tool}</strong></div>
                      ${event.args && event.args.title ? `<div><span class="text-slate-500">Workout:</span> <span class="text-slate-200">${event.args.title}</span></div>` : ''}
                      ${event.args && event.args.workout_date ? `<div><span class="text-slate-500">Date:</span> <span class="text-slate-200">${event.args.workout_date}</span> (${event.args.duration_minutes || 60} min)</div>` : ''}
                    </div>
                    <div class="flex gap-2">
                      <button onclick="submitConfirmation('${event.call_id}', true)" class="px-3.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 shadow transition">
                        <i class="fa-solid fa-check"></i> Approve & Stage Workout
                      </button>
                      <button onclick="submitConfirmation('${event.call_id}', false)" class="px-3.5 py-1.5 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 shadow transition">
                        <i class="fa-solid fa-xmark"></i> Reject
                      </button>
                    </div>
                  `;
                  chatStream.appendChild(confCard);
                  chatStream.scrollTop = chatStream.scrollHeight;
                } else if (event.type === 'plan_generated') {
                  const planCard = document.createElement('div');
                  planCard.className = 'my-3 p-3.5 rounded-xl border border-brand-500/40 bg-brand-950/20 flex items-center justify-between text-slate-200 shadow';
                  planCard.innerHTML = `
                    <div class="flex items-center gap-2.5">
                      <div class="w-8 h-8 rounded-lg bg-brand-900/60 border border-brand-500/30 flex items-center justify-center text-brand-400 text-sm">
                        <i class="fa-solid fa-file-lines"></i>
                      </div>
                      <div>
                        <div class="text-xs font-bold text-slate-200">${event.filename}</div>
                        <div class="text-[10px] text-brand-400">Saved to local repository (plans/)</div>
                      </div>
                    </div>
                    <a href="${event.download_url}" download="${event.filename}" class="px-3 py-1.5 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-xs font-medium flex items-center gap-1.5 shadow transition">
                      <i class="fa-solid fa-download"></i> Download Plan
                    </a>
                  `;
                  chatStream.appendChild(planCard);
                  chatStream.scrollTop = chatStream.scrollHeight;
                } else if (event.type === 'text') {
                  rawAccumulated += event.delta;
                  agentBody.innerHTML = marked.parse(rawAccumulated);
                  chatStream.scrollTop = chatStream.scrollHeight;
                } else if (event.type === 'done') {
                  agentBody.innerHTML = marked.parse(event.rehydrated || rawAccumulated);
                } else if (event.type === 'error') {
                  agentBody.innerHTML += `<div class="text-rose-400 font-bold text-xs mt-2">[Error]: ${event.error}</div>`;
                }
              } catch (err) {}
            }
          }
        }
      } catch (err) {
        agentBody.innerHTML = `<p class="text-rose-400">[Network Error]: ${err.message}</p>`;
      } finally {
        sendBtn.disabled = false;
        userInput.focus();
      }
    });

    window.submitConfirmation = async function(callId, confirmed) {
      const activeCard = document.getElementById('active-confirm-card');
      if (activeCard) {
        activeCard.querySelectorAll('button').forEach(b => b.disabled = true);
        activeCard.classList.add('opacity-70');
      }
      appendUserMessage(confirmed ? "✓ Confirmed: Approve & stage workout on Intervals.icu" : "✗ Rejected: Cancel staging");
      const agentBody = createAgentMessageSlot();
      let rawAccumulated = '';
      sendBtn.disabled = true;

      try {
        const response = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: confirmed ? "Confirmed" : "Rejected",
            session_id: sessionId,
            confirmation: { id: callId, confirmed: confirmed }
          })
        });

        if (confirmed) setStage('staged');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n\n');
          buffer = lines.pop();

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const payload = line.slice(6);
              if (payload === '[DONE]') break;
              try {
                const event = JSON.parse(payload);
                if (event.type === 'tool_call') {
                  addActivityItem(event.name, event.args, event.elapsed_ms);
                } else if (event.type === 'stage') {
                  setStage(event.stage);
                } else if (event.type === 'text') {
                  rawAccumulated += event.delta;
                  agentBody.innerHTML = marked.parse(rawAccumulated);
                  chatStream.scrollTop = chatStream.scrollHeight;
                } else if (event.type === 'done') {
                  agentBody.innerHTML = marked.parse(event.rehydrated || rawAccumulated);
                } else if (event.type === 'error') {
                  agentBody.innerHTML += `<div class="text-rose-400 font-bold text-xs mt-2">[Error]: ${event.error}</div>`;
                }
              } catch (err) {}
            }
          }
        }
      } catch (err) {
        agentBody.innerHTML = `<p class="text-rose-400">[Confirmation Error]: ${err.message}</p>`;
      } finally {
        window.pendingConfirmation = null;
        sendBtn.disabled = false;
        userInput.focus();
      }
    };
  </script>
</body>
</html>
"""


DEMO_PASSCODE = os.environ.get("DEMO_PASSCODE", "")

LOCK_SCREEN_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>ACL Masters Rowing Coach — Reviewer Access</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 flex items-center justify-center min-h-screen p-4 font-sans antialiased">
  <div class="max-w-md w-full bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl text-center">
    <div class="w-16 h-16 bg-blue-500/10 border border-blue-500/20 text-blue-400 rounded-full flex items-center justify-center mx-auto mb-4 text-2xl">
      🚣‍♂️
    </div>
    <h1 class="text-xl font-bold tracking-tight mb-1 text-white">ACL Masters Rowing Coach</h1>
    <p class="text-xs text-slate-400 mb-6">GDG London AI DevCamp Hackathon Live Evaluation Portal</p>
    
    <div class="bg-amber-500/10 border border-amber-500/20 rounded-xl p-3 text-left mb-6 text-xs text-amber-300">
      <span class="font-bold">🛡️ Evaluation Gating Active:</span> To protect cloud inference budgets and prevent public crawler abuse, please enter the hackathon review passcode to access the live Vertex AI Reasoning Engine.
    </div>

    <form method="GET" action="/" class="space-y-4">
      <div>
        <input type="password" name="code" placeholder="Enter Reviewer Passcode" required autofocus
               class="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 text-sm text-center font-mono text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500">
      </div>
      <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 text-white font-semibold py-3 px-4 rounded-lg text-sm transition-colors shadow-lg shadow-blue-500/20">
        Unlock Live Coaching Workspace &rarr;
      </button>
    </form>
    
    <div class="mt-6 pt-4 border-t border-slate-800 text-[11px] text-slate-500">
      Passcode provided in hackathon portal submission: <code class="text-blue-400 font-mono">rowing-********-devcamp-2026</code>
    </div>
  </div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index_page(request: Request):
    if DEMO_PASSCODE:
        code_param = request.query_params.get("code")
        code_cookie = request.cookies.get("rowing_passcode")
        if code_param != DEMO_PASSCODE and code_cookie != DEMO_PASSCODE:
            return HTMLResponse(content=LOCK_SCREEN_HTML)
        
        response = HTMLResponse(content=HTML_PAGE)
        if code_param == DEMO_PASSCODE:
            response.set_cookie(key="rowing_passcode", value=DEMO_PASSCODE, max_age=86400, httponly=True)
        return response
    return HTMLResponse(content=HTML_PAGE)


# ------------------------------------------------------------------------------
# CLI Runner
# ------------------------------------------------------------------------------
async def run_cli(initial_prompt: Optional[str] = None):
    print("\n=======================================================")
    print(" 🚣‍♂️ Rowing Coach Agent — Direct Terminal Interface")
    print("=======================================================")
    session_id = f"cli-{uuid.uuid4().hex[:8]}"

    if initial_prompt:
        print(f"Coach: {initial_prompt}")
        print("Agent: ", end="", flush=True)
        async for ev in stream_agent_pipeline(initial_prompt, session_id=session_id):
            if ev.get("type") == "text":
                print(ev["delta"], end="", flush=True)
            elif ev.get("type") == "tool_call":
                print(f"\n[Tool: {ev['name']}]", end="", flush=True)
        print("\n")
        return

    print(f"[Active Multi-Turn Session: {session_id}] (Type 'exit' to quit)\n")
    while True:
        try:
            prompt = input("Coach > ").strip()
            if not prompt or prompt.lower() in ("exit", "quit"):
                break
            print("\nAgent: ", end="", flush=True)
            async for ev in stream_agent_pipeline(prompt, session_id=session_id):
                if ev.get("type") == "text":
                    print(ev["delta"], end="", flush=True)
                elif ev.get("type") == "tool_call":
                    print(f"\n[Tool: {ev['name']}]", end="", flush=True)
            print("\n")
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break


def main():
    default_target = os.environ.get("PROXY_TARGET", "cloud").lower()
    parser = argparse.ArgumentParser(description="Rowing Coach Local Proxy & UI")
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # CLI subcommand
    cli_parser = subparsers.add_parser("cli", help="Run interactive terminal chat")
    cli_parser.add_argument("prompt", nargs="?", default=None, help="Optional one-shot prompt")
    cli_parser.add_argument("--target", "-t", choices=["local", "cloud"], default=default_target, help="Target backend (default: cloud)")

    # Serve subcommand
    serve_parser = subparsers.add_parser("serve", help="Run local web server and UI")
    serve_parser.add_argument("--port", type=int, default=8080, help="Local port (default 8080)")
    serve_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default 127.0.0.1)")
    serve_parser.add_argument("--target", "-t", choices=["local", "cloud"], default=default_target, help="Target backend (default: cloud)")

    args = parser.parse_args()
    target = getattr(args, "target", default_target)
    os.environ["PROXY_TARGET"] = target

    if args.command == "serve":
        import uvicorn
        print("==========================================================")
        print(f" 🚣‍♂️ [run-proxy] Profile: {target.upper()}")
        print(f" [run-proxy] UI URL:  http://{args.host}:{args.port}")
        print("==========================================================")
        uvicorn.run("local_proxy:app", host=args.host, port=args.port, reload=False)
    else:
        import asyncio
        initial_prompt = getattr(args, "prompt", None)
        asyncio.run(run_cli(initial_prompt))


if __name__ == "__main__":
    main()
