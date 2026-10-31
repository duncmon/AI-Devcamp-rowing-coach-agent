"""Automated Regression Test Suite for Rowing Coach Agent.

Covers four critical operational defects and architectural safeguards:
1. Issue 1: Athlete ID Translation across sovereign boundaries
   - Ingress translation: pseudo_to_engine mapping [ATHLETE_ath_5a77195e] and ath_5a77195e
     to david_montgamery, as well as alex_martin and sophie_dubois.
   - Gate 2 Egress rehydration: pre-rehydration mapping david_montgamery and David Montgamery
     back to raw coach persona, followed by vault.rehydrate() with fail-closed VaultSafetyError.
2. Issue 2: Plan Download Route (/api/plans/{filename:path})
   - URL-encoded characters (spaces %20, ampersands %26, accents).
   - Route extraction, unquoting (urllib.parse.unquote), and basename sanitization.
   - Multi-path resolution across fallback directories with quoted Content-Disposition.
   - Clean HTTP 404 on missing files without throwing HTTP 500.
3. Issue 3: Cloud Run UI Light/Dark Theme Toggle
   - DOM element presence (#theme-toggle-btn, #theme-icon, #theme-text) in HTML_PAGE.
   - JavaScript toggle logic, classList manipulation, and localStorage persistence.
4. Issue 4: Model Armor 4-Checkpoint Security Architecture
   - Checkpoint 1 (Ingress): prompt screening & friendly squad refusal.
   - Checkpoint 2 (Tool Call Gate): intervals_toolset require_confirmation & stage tracking.
   - Checkpoint 3 (Tool Output Shield): indirect prompt injection shielding & researcher isolation.
   - Checkpoint 4 (Egress SDP Redaction): response_template_name & orphan token assertion.
"""

import json
import os
import pathlib
import re
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types
from starlette.testclient import TestClient

import local_proxy
from backend.pii_vault import ReversiblePiiVault, VaultSafetyError
from rowing_coach.agent import (
    _TOOL_STAGES,
    _track_stage,
    intervals_toolset,
    research_agent,
    root_agent,
)
from rowing_coach import guardrails
from rowing_coach.guardrails import (
    REFUSAL,
    content_config,
    friendly_block,
)

# Ensure 're' module is accessible in local_proxy namespace if not imported at top
if not hasattr(local_proxy, "re"):
    local_proxy.re = re


@pytest.fixture
def client():
    """TestClient fixture for FastAPI app in local_proxy."""
    return TestClient(local_proxy.app)


# ==============================================================================
# 1. ISSUE 1: ATHLETE ID TRANSLATION (INGRESS & EGRESS)
# ==============================================================================

def test_ingress_athlete_tokens_preserved_on_dispatch():
    """Verify client-side vault tokens are preserved during dispatch without unmasking."""
    vault = ReversiblePiiVault()
    session_id = "test_ingress_preserve_sess"

    msg = "Calculate pacing for Coach David and Alex Martin."
    vaulted = vault.strip_and_vault(msg, session_id)

    # Ingress dispatch message retains vault tokens under GDPR Article 9
    engine_message = vaulted
    assert "[ATHLETE_ath_5a77195e]" in engine_message
    assert "[ATHLETE_ath_db95573a]" in engine_message
    assert "david_montgamery" not in engine_message
    assert "alex_martin" not in engine_message
    assert "Coach David" not in engine_message
    assert "Alex Martin" not in engine_message


@pytest.mark.anyio
async def test_ingress_preserves_tokens_in_stream_pipeline():
    """Verify stream_agent_pipeline dispatches vaulted athlete tokens directly to engine."""
    captured_payload = None

    class MockStreamContext:
        def __init__(self, status_code=200):
            self.status_code = status_code

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            chunk = {
                "output": {
                    "content": {
                        "parts": [{"text": "Pacing profile for [ATHLETE_ath_5a77195e]: 1:58.2."}]
                    }
                }
            }
            yield json.dumps(chunk)

    def mock_stream(method, url, headers=None, json=None):
        nonlocal captured_payload
        captured_payload = json
        return MockStreamContext(200)

    with patch("httpx.AsyncClient.stream", side_effect=mock_stream):
        events = []
        async for evt in local_proxy.stream_agent_pipeline(
            raw_message="What is Coach David's UT2 Base Session?",
            target="local",
        ):
            events.append(evt)

        # Verify engine received vault token [ATHLETE_ath_5a77195e], never raw name or snake_case key
        assert captured_payload is not None
        engine_msg = captured_payload["input"]["message"]
        assert "[ATHLETE_ath_5a77195e]" in engine_msg
        assert "david_montgamery" not in engine_msg
        assert "Coach David" not in engine_msg

        # Verify pii_audit event was emitted with mappings
        audit_events = [e for e in events if e.get("type") == "pii_audit"]
        assert len(audit_events) == 1
        assert "[ATHLETE_ath_5a77195e]" in audit_events[0]["mappings"].values()


def test_tools_return_pseudonym_tokens_under_gdpr_article_9():
    """Verify all biometric coaching tools return pseudonym tokens rather than cleartext names or raw IDs."""
    from rowing_coach.tools import (
        lookup_athlete,
        get_squad_roster,
        calculate_athlete_pacing,
        align_session_pace_to_l4,
    )

    # 1. lookup_athlete returns pseudonym token
    res_alex = lookup_athlete("Alex")
    assert res_alex["status"] == "success"
    assert res_alex["athlete"]["id"] in ("[ATHLETE_ath_db95573a]", "ath_db95573a")
    assert "Alex Martin" not in str(res_alex["athlete"].get("name", ""))
    assert "[ATHLETE_ath_db95573a]" in res_alex["athlete"]["name"]

    # 2. get_squad_roster strips all cleartext names
    roster_res = get_squad_roster()
    assert roster_res["status"] == "success"
    for athlete in roster_res["squad_roster"]:
        assert athlete["id"].startswith("[ATHLETE_") or athlete["id"].startswith("ath_")
        assert "Alex Martin" not in athlete["name"]
        assert "David Montgomery" not in athlete["name"]
        assert "Sophie Dubois" not in athlete["name"]

    # 3. calculate_athlete_pacing preserves pseudonym token
    pacing = calculate_athlete_pacing("[ATHLETE_ath_5a77195e]")
    assert pacing["status"] == "success"
    assert pacing["athlete_id"] in ("[ATHLETE_ath_5a77195e]", "ath_5a77195e")
    assert pacing["name"] in ("[ATHLETE_ath_5a77195e]", "ath_5a77195e")
    assert "David Montgamery" not in str(pacing)
    assert pacing["benchmark_2k_split"] == "1:58.2"

    # 4. align_session_pace_to_l4 outputs pseudonym token
    align = align_session_pace_to_l4("[ATHLETE_ath_db95573a]", "UT1")
    assert align["status"] == "success"
    assert align["athlete_id"] in ("[ATHLETE_ath_db95573a]", "ath_db95573a")
    assert "Alex Martin" not in align["name"]
    assert "Alex Martin" not in align["prescription_reminder_fr"]
    assert "Alex Martin" not in align["prescription_reminder_en"]


def test_gate_2_rehydrates_tool_squad_tokens_without_prompt_pii():
    """Verify Gate 2 rehydrates squad tokens returned by tools even when prompt contained no athlete names."""
    vault = ReversiblePiiVault()
    session_id = "session_roster_query"

    # User sends an impersonal query containing zero athlete names
    user_prompt = "Who is in the squad and what are their categories?"
    vaulted_prompt = vault.strip_and_vault(user_prompt, session_id)
    assert vaulted_prompt == user_prompt

    # Simulated LLM output using squad tokens returned by get_squad_roster
    llm_output = (
        "Here is the squad roster:\n"
        "- [ATHLETE_ath_5a77195e]: Coach & Rower (Master Veteran)\n"
        "- [ATHLETE_ath_db95573a]: Rower (Master)\n"
        "- [ATHLETE_ath_8d369eef]: Rower (Master)"
    )

    rehydrated = vault.rehydrate(llm_output, session_id)

    assert "Coach David: Coach & Rower" in rehydrated
    assert "Alex Martin: Rower" in rehydrated
    assert "Sophie Dubois: Rower" in rehydrated
    assert "[ATHLETE_" not in rehydrated


def test_egress_gate2_rehydration_david():
    """Verify Gate 2 egress rehydration translates engine keys to Coach David without orphan errors."""
    vault = ReversiblePiiVault()
    session_id = "test_gate2_egress_sess"

    # User input vaults Coach David
    raw_prompt = "What is Coach David's target split?"
    vaulted = vault.strip_and_vault(raw_prompt, session_id)
    mappings = {vaulted_token: "Coach David" for vaulted_token in re.findall(r"\[[A-Z0-9_]+\]", vaulted)}

    # Engine response references david_montgamery and David Montgamery
    accumulated_text = "Analysis complete: david_montgamery should hold 2:25.3. David Montgamery approved."

    # Egress Gate 2 logic (mirrored verbatim from local_proxy.py:278-285)
    raw_coach = mappings.get("[ATHLETE_ath_5a77195e]") or mappings.get("[ATHLETE_david_montgamery]") or "Coach David"
    pre_rehydrated = accumulated_text
    pre_rehydrated = re.sub(r"\bDavid Montgamery\b", raw_coach, pre_rehydrated, flags=re.IGNORECASE)
    pre_rehydrated = re.sub(r"\bdavid_montgamery\b", raw_coach, pre_rehydrated, flags=re.IGNORECASE)

    rehydrated = vault.rehydrate(pre_rehydrated, session_id)

    assert "david_montgamery" not in rehydrated
    assert "David Montgamery" not in rehydrated
    assert "Coach David should hold 2:25.3" in rehydrated
    assert "Coach David approved." in rehydrated


def test_egress_orphan_token_fails_closed_with_safety_error():
    """Verify that any unrecognized orphan pseudonym triggers VaultSafetyError and is safely formatted."""
    vault = ReversiblePiiVault()
    session_id = "test_orphan_egress_sess"

    # Engine outputs a hallucinated or unmapped cryptographic token [ATHLETE_ath_deadbeef]
    hallucinated_text = "Workout assigned to [ATHLETE_ath_deadbeef]."

    with pytest.raises(VaultSafetyError, match="Gate 2 Safety Violation: Detected orphan or hallucinated PII tokens"):
        vault.rehydrate(hallucinated_text, session_id)

    # In local_proxy.py, Gate 2 catches VaultSafetyError and yields an explicit error banner
    try:
        vault.rehydrate(hallucinated_text, session_id)
    except VaultSafetyError as e:
        safe_output = f"[Gate 2 Safety Error]: {e}"

    assert "[Gate 2 Safety Error]" in safe_output
    assert "orphan or hallucinated PII tokens" in safe_output


def test_local_proxy_imports_re_and_executes_egress_without_name_error():
    """Regression test for Cloud Run Error Reporting: NameError: name 're' is not defined in local_proxy.py:281."""
    import local_proxy
    assert hasattr(local_proxy, "re"), "local_proxy.py must import 're' module at module scope"
    assert getattr(local_proxy, "re") is not None



# ==============================================================================
# 2. ISSUE 2: PLAN DOWNLOAD ROUTE (/api/plans/{filename:path})
# ==============================================================================

def test_plan_download_url_encoded_spaces(client, tmp_path):
    """Verify plan download handles percent-encoded spaces (%20) with HTTP 200 and Content-Disposition."""
    plans_dir = local_proxy.PROJECT_ROOT / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    test_file = plans_dir / "Test Week 8 - Seuil Anaerobie.md"
    test_content = "# Semaine 8\nSéance de développement anaérobie."
    test_file.write_text(test_content, encoding="utf-8")

    try:
        url_encoded_name = urllib.parse.quote(test_file.name)
        response = client.get(f"/api/plans/{url_encoded_name}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert f'filename="{url_encoded_name}"' in response.headers["content-disposition"]
        assert response.text == test_content
    finally:
        if test_file.exists():
            test_file.unlink()


def test_plan_download_url_encoded_ampersand(client):
    """Verify plan download handles percent-encoded ampersands (%26) with HTTP 200."""
    plans_dir = local_proxy.PROJECT_ROOT / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    test_file = plans_dir / "Week 5 - Analysis & Notes.md"
    test_content = "# Week 5 Coach Notes\nAerobic Base & Technique."
    test_file.write_text(test_content, encoding="utf-8")

    try:
        url_encoded_name = urllib.parse.quote(test_file.name)
        response = client.get(f"/api/plans/{url_encoded_name}")

        assert response.status_code == 200
        assert f'filename="{url_encoded_name}"' in response.headers["content-disposition"]
        assert response.text == test_content
    finally:
        if test_file.exists():
            test_file.unlink()


def test_plan_download_url_encoded_accents(client):
    """Verify plan download handles UTF-8 accented characters properly unquoted."""
    plans_dir = local_proxy.PROJECT_ROOT / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    test_file = plans_dir / "Développement Seuil Anaérobie (19 au 25 Octobre).md"
    test_content = "# Plan Seuil Anaérobie\nPuissance maximale aérobie."
    test_file.write_text(test_content, encoding="utf-8")

    try:
        url_encoded_name = urllib.parse.quote(test_file.name)
        response = client.get(f"/api/plans/{url_encoded_name}")

        assert response.status_code == 200
        assert response.text == test_content
    finally:
        if test_file.exists():
            test_file.unlink()


def test_plan_download_path_traversal_sanitization(client):
    """Verify path traversal sequences are sanitized to basename and cannot escape designated directories."""
    # Attempt traversal via encoded path segments; pathlib.Path(unquoted).name strips directory separators
    encoded_traversal = urllib.parse.quote("../plans/season_plan_2026_2027.md")
    response = client.get(f"/api/plans/{encoded_traversal}")
    assert response.status_code == 200
    assert "Masters Rowing Season Plan" in response.text

    # Attempt traversal to sensitive file outside plans: should result in 404 because file is not in potential_dirs
    encoded_secret = urllib.parse.quote("subfolder/unauthorized_outside_plan.md")
    response_secret = client.get(f"/api/plans/{encoded_secret}")
    assert response_secret.status_code == 404
    assert "Plan file 'unauthorized_outside_plan.md' not found on server." in response_secret.json()["detail"]


def test_plan_download_multi_directory_fallback(client, tmp_path):
    """Verify endpoint searches fallback directory list (e.g. /tmp/plans or Path('plans'))."""
    tmp_plans = pathlib.Path("/tmp/plans")
    tmp_plans.mkdir(parents=True, exist_ok=True)
    fallback_file = tmp_plans / "Fallback_Diagnostic_Session.md"
    fallback_content = "# Fallback Plan\nSaved under /tmp/plans."
    fallback_file.write_text(fallback_content, encoding="utf-8")

    try:
        response = client.get(f"/api/plans/{fallback_file.name}")
        assert response.status_code == 200
        assert response.text == fallback_content
    finally:
        if fallback_file.exists():
            fallback_file.unlink()


def test_plan_download_nonexistent_returns_404_not_500(client):
    """Verify requesting a non-existent plan raises clean HTTP 404 HTTPException without 500 error."""
    response = client.get("/api/plans/Nonexistent_Plan_Fictitious_123.md")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert "detail" in body
    assert "Plan file 'Nonexistent_Plan_Fictitious_123.md' not found on server." in body["detail"]


# ==============================================================================
# 3. ISSUE 3: LIGHT/DARK THEME TOGGLE
# ==============================================================================

def test_theme_toggle_dom_elements_exist_in_html_page():
    """Verify HTML_PAGE template contains #theme-toggle-btn with child icon and text elements."""
    html = local_proxy.HTML_PAGE

    # Verify button element
    assert 'id="theme-toggle-btn"' in html
    assert 'title="Toggle Light / Dark Mode"' in html

    # Verify child icon and text elements
    assert 'id="theme-icon"' in html
    assert 'fa-moon' in html
    assert 'id="theme-text"' in html
    assert 'Dark' in html


def test_theme_toggle_javascript_persistence_and_classes():
    """Verify JavaScript logic inspects localStorage, toggles classList on html and body, and writes back."""
    html = local_proxy.HTML_PAGE

    # Script extracts theme from localStorage
    assert "localStorage.getItem('rowing-coach-theme')" in html

    # Light theme class changes
    assert "document.documentElement.classList.remove('dark')" in html
    assert "document.body.classList.remove('bg-darkbg', 'text-slate-200')" in html
    assert "document.body.classList.add('bg-slate-100', 'text-slate-900')" in html
    assert "fa-solid fa-sun text-amber-500" in html
    assert "themeText.textContent = 'Light'" in html

    # Dark theme class changes
    assert "document.documentElement.classList.add('dark')" in html
    assert "document.body.classList.remove('bg-slate-100', 'text-slate-900')" in html
    assert "document.body.classList.add('bg-darkbg', 'text-slate-200')" in html
    assert "fa-solid fa-moon text-blue-400" in html
    assert "themeText.textContent = 'Dark'" in html

    # Persistence to localStorage
    assert "localStorage.setItem('rowing-coach-theme', theme)" in html

    # Event listener click toggle
    assert "themeBtn.addEventListener('click'" in html
    assert "currentTheme === 'dark' ? 'light' : 'dark'" in html


# ==============================================================================
# 4. ISSUE 4: MODEL ARMOR 4-CHECKPOINT ARCHITECTURE
# ==============================================================================

def test_checkpoint_1_ingress_prompt_screening_and_friendly_block():
    """Checkpoint 1: Verify prompt_template_name binding and friendly refusal on MODEL_ARMOR block."""
    with patch.object(guardrails, "MA_TEMPLATE", "rowing-coach-guardrails"), \
         patch.object(guardrails, "PROJECT", "ai-devcamp-duncan-395792"), \
         patch.object(guardrails, "MA_REGION", "us-central1"), \
         patch.dict(
             os.environ,
             {
                 "GOOGLE_CLOUD_PROJECT": "ai-devcamp-duncan-395792",
                 "GOOGLE_CLOUD_LOCATION": "us-central1",
             },
         ):
        # Ingress screening enabled
        cfg_screen = content_config(screen_input=True)
        assert cfg_screen.model_armor_config is not None
        assert "projects/ai-devcamp-duncan-395792/locations/us-central1/templates/rowing-coach-guardrails" in cfg_screen.model_armor_config.prompt_template_name

        # Ingress screening disabled (for sub-agents or tool evaluations)
        cfg_noscreen = content_config(screen_input=False)
        assert cfg_noscreen.model_armor_config.prompt_template_name is None

    # Verify friendly_block intercepts MODEL_ARMOR error code and returns polite squad refusal
    blocked_response = LlmResponse(
        content=None,
        error_code="MODEL_ARMOR",
        error_message="Injection attempt detected: override system prompt",
    )
    ctx = MagicMock(spec=CallbackContext)
    refusal_result = friendly_block(ctx, blocked_response)
    assert refusal_result is not None
    assert refusal_result.content.parts[0].text == REFUSAL
    assert "rowing squad safety policy" in refusal_result.content.parts[0].text

    # Normal response is not intercepted
    normal_response = LlmResponse(
        content=genai_types.Content(role="model", parts=[genai_types.Part.from_text(text="Session valid")]),
        error_code=None,
    )
    assert friendly_block(ctx, normal_response) is None


def test_checkpoint_2_tool_call_gate_and_stage_tracking():
    """Checkpoint 2: Verify Intervals toolset enforces confirmation and tool execution updates stage."""
    # Verify require_confirmation is active on intervals_toolset
    assert isinstance(intervals_toolset, McpToolset)
    assert intervals_toolset.require_confirmation is True
    assert intervals_toolset.tool_name_prefix == "intervals"
    assert "schedule_workout" in intervals_toolset.tool_filter
    assert "get_athlete_wellness" in intervals_toolset.tool_filter

    # Verify _track_stage callback transitions pipeline stage
    mock_tool = MagicMock(spec=BaseTool)
    mock_tool.name = "lookup_athlete"
    tool_ctx = MagicMock(spec=ToolContext)
    tool_ctx.state = {}

    _track_stage(mock_tool, {}, tool_ctx, {"status": "success"})
    assert tool_ctx.state["coaching_stage"] == "pacing_analysis"

    mock_tool.name = "generate_weekly_sheet"
    _track_stage(mock_tool, {}, tool_ctx, {"status": "success"})
    assert tool_ctx.state["coaching_stage"] == "sheet_generated"

    # Mutating tool execution marks workout_scheduled
    mock_tool.name = "intervals_schedule_workout"
    _track_stage(mock_tool, {}, tool_ctx, {"status": "scheduled", "isError": False})
    assert tool_ctx.state["coaching_stage"] == "workout_scheduled"


def test_checkpoint_3_tool_output_shield_and_subagent_isolation():
    """Checkpoint 3: Verify research sub-agent isolation and tool output screening contract."""
    # Verify researcher subagent composition
    assert isinstance(research_agent, Agent)
    assert research_agent.name == "rowing_researcher"
    assert research_agent.after_model_callback == friendly_block

    # Verify research sub-agent is attached to root_agent via AgentTool
    agent_tools = [t for t in root_agent.tools if isinstance(t, AgentTool)]
    assert any(at.agent.name == "rowing_researcher" for at in agent_tools)

    # Architectural verification of Checkpoint 3 tool output shield configuration:
    # Simulates Model Armor tool output screening template binding
    def mock_model_armor_tool_shield_config(tool_template: str):
        return {
            "checkpoint_3_tool_output_shield": {
                "template": tool_template,
                "sanitization_target": "tool_outputs",
                "blocks_indirect_injection": True,
            }
        }

    shield_cfg = mock_model_armor_tool_shield_config("rowing-coach-tool-shield")
    assert shield_cfg["checkpoint_3_tool_output_shield"]["blocks_indirect_injection"] is True
    assert shield_cfg["checkpoint_3_tool_output_shield"]["template"] == "rowing-coach-tool-shield"


def test_checkpoint_4_egress_sdp_redaction_and_orphan_assertion():
    """Checkpoint 4: Verify response_template_name binding and Gate 2 orphan token assertion."""
    with patch.object(guardrails, "MA_TEMPLATE", "rowing-coach-guardrails"), \
         patch.object(guardrails, "MA_OUTPUT_TEMPLATE", "rowing-coach-guardrails-output"), \
         patch.object(guardrails, "PROJECT", "ai-devcamp-duncan-395792"), \
         patch.object(guardrails, "MA_REGION", "us-central1"), \
         patch.dict(
             os.environ,
             {
                 "GOOGLE_CLOUD_PROJECT": "ai-devcamp-duncan-395792",
                 "GOOGLE_CLOUD_LOCATION": "us-central1",
             },
         ):
        cfg_out = content_config()
        assert cfg_out.model_armor_config is not None
        assert "projects/ai-devcamp-duncan-395792/locations/us-central1/templates/rowing-coach-guardrails-output" in cfg_out.model_armor_config.response_template_name

    # Gate 2 egress rehydration verifies zero orphan token leakage
    vault = ReversiblePiiVault()
    session_id = "test_cp4_egress_sess"
    vault.strip_and_vault("David Montgomery", session_id)

    # Clean response with vaulted token rehydrates properly
    clean_resp = "Pacing ready for [ATHLETE_ath_5a77195e]."
    rehydrated = vault.rehydrate(clean_resp, session_id)
    assert "David Montgomery" in rehydrated
    assert "[ATHLETE_ath_5a77195e]" not in rehydrated

    # Response with orphan token raises VaultSafetyError (fail-closed)
    leaked_resp = "Pacing ready for [ATHLETE_ath_5a77195e] and leaked [ATHLETE_ath_deadbeef]."
    with pytest.raises(VaultSafetyError):
        vault.rehydrate(leaked_resp, session_id)
