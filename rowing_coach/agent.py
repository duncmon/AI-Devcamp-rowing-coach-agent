"""Rowing Coach ADK Agent.

Implements the Master Coach Agent connecting:
- Custom hand-written tools (physics, L4 tables, athlete lookups, sheet generator)
- SkillToolset loading 'masters-rowing-rules' (Sam Dutney system)
- AgentTool isolating 'google_search' in 'rowing_researcher' sub-agent
- McpToolset connecting local FastMCP Intervals.icu server with require_confirmation=True
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Any, Dict, Optional

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.skills import load_skill_from_dir
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.tools.tool_context import ToolContext
from mcp import StdioServerParameters

from .tools import (
    align_session_pace_to_l4,
    calculate_athlete_pacing,
    generate_coach_notes,
    generate_weekly_sheet,
    get_season_schedule,
    lookup_athlete,
)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "skills"
MCP_SERVER = PROJECT_ROOT / "mcp" / "intervals_server.py"

# ------------------------------------------------------------------------------
# 1. Agent Skills (Sam Dutney Rules & Principles)
# ------------------------------------------------------------------------------
skill_toolset = SkillToolset(
    skills=[
        load_skill_from_dir(SKILLS_DIR / "masters-rowing-rules"),
    ],
)

# ------------------------------------------------------------------------------
# 2. Intervals.icu MCP Toolset (Gated with require_confirmation=True)
# ------------------------------------------------------------------------------
intervals_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_SERVER)],
            env={
                "DRY_RUN": os.environ.get("DRY_RUN", "true"),
                "INTERVALS_API_KEY": os.environ.get("INTERVALS_API_KEY", ""),
                "INTERVALS_ATHLETE_ID": os.environ.get("INTERVALS_ATHLETE_ID", "0"),
            },
        ),
    ),
    tool_filter=["get_athlete_wellness", "schedule_workout"],
    tool_name_prefix="intervals",
    require_confirmation=True,
)

ROOT_MODEL = os.environ.get("MODEL", "gemini-2.5-flash")
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "gemini-2.5-flash-lite")

# ------------------------------------------------------------------------------
# 3. Isolated Research Sub-Agent via AgentTool
# ------------------------------------------------------------------------------
research_agent = Agent(
    name="rowing_researcher",
    model=RESEARCH_MODEL,
    description="Researches weather, regattas (Ligue Sud PACA, FFAviron), coastal/river boat rigging, and coaching literature.",
    instruction="""You are a specialized rowing researcher assisting Coach David Montgamery at CASC.
Your research domains include:
1. Local weather and water conditions: Saint-Chamas base on the Étang de Berre (Plage des Cabassons), Lac des Buissonnets (Manosque), and regional rowing venues.
2. Official regatta & rally calendars and racing rules: Ligue d'Aviron Région Sud (https://www.avironsudpaca.com/calendrier-sportif.html), FFAviron competitions (https://www.ffaviron.fr/competitions/), and national events (https://www.ffaviron.fr/suivre-les-competitions/tous-les-evenements-ffa/). Help check whether sessions need to be displaced for competing rowers.
3. Rigging geometry and boat setups: Coastal and river boats, especially span, pin-to-heel, inboard, pitch, and oar length for Masters and shorter athletes.
4. Coaching and exercise physiology updates: Sam Dutney, Jack Burns, Clark Performance Rowing, Élodie Ravera, and related evidence-based endurance methodologies.

Search the web and provide concise, factual, actionable summaries with dates, conditions, or numerical specifications.""",
    tools=[google_search],
)

# ------------------------------------------------------------------------------
# 4. State Callbacks & Pipeline Tracking
# ------------------------------------------------------------------------------
_TOOL_STAGES = {
    "get_season_schedule": "curriculum_consulted",
    "rowing_researcher": "researching",
    "load_skill": "consulting_rules",
    "lookup_athlete": "pacing_analysis",
    "calculate_athlete_pacing": "pacing_analysis",
    "align_session_pace_to_l4": "pacing_analysis",
    "generate_weekly_sheet": "sheet_generated",
    "generate_coach_notes": "notes_generated",
}


def _init_stage(callback_context: CallbackContext) -> None:
    if "coaching_stage" not in callback_context.state:
        callback_context.state["coaching_stage"] = "consulting"


def _track_stage(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, tool_response: dict
) -> Optional[dict]:
    if tool.name in _TOOL_STAGES:
        tool_context.state["coaching_stage"] = _TOOL_STAGES[tool.name]
    elif tool.name == "intervals_schedule_workout" and not tool_response.get("isError"):
        tool_context.state["coaching_stage"] = "workout_scheduled"
    return None


def _stage_after_agent(callback_context: CallbackContext) -> None:
    current = callback_context.state.get("coaching_stage")
    if current in ("consulting_rules", "pacing_analysis", "curriculum_consulted"):
        callback_context.state["coaching_stage"] = "plan_ready"


# ------------------------------------------------------------------------------
# 5. Root Agent Assembly
# ------------------------------------------------------------------------------
tools = [
    get_season_schedule,
    lookup_athlete,
    calculate_athlete_pacing,
    align_session_pace_to_l4,
    generate_weekly_sheet,
    generate_coach_notes,
    AgentTool(agent=research_agent),
    skill_toolset,
    intervals_toolset,
]

root_agent = Agent(
    name="rowing_coach",
    model=ROOT_MODEL,
    description="CASC Masters Squad Coach Assistant implementing Sam Dutney's rowing training system.",
    instruction="""You are the AI Coach Assistant for Coach David Montgamery and the CASC Masters Rowing Squad, strictly implementing Sam Dutney's rowing training system.
You plan sessions, calculate precision training splits, generate weekly squad sheets, and stage workouts on Intervals.icu.

Core Rules & Workflow:
1. Always load and adhere to the 'masters-rowing-rules' skill before finalizing any training advice, split calculations, or workout prescriptions.
2. Season Curriculum & Progression Authority:
   - Before answering questions regarding weekly programming, current progression, calendar dates, deload status, or competition targets, ALWAYS invoke `get_season_schedule`.
   - Current Date Context: October 3, 2026.
   - Current Week: Week 4 (Sep 28 to Oct 4, 2026) is Assimilation Deload 1 (-30% volume).
   - Upcoming Week: Week 5 (Oct 5 to Oct 11, 2026) is Block 2 Kickoff (Sam Dutney Anaerobic Threshold Intro 3x7' + Wolverine L4 Overload Sequence 186). WEEK 5 IS NOT A DELOAD WEEK!
   - Deload weeks across the 13-week Phase 1 are strictly: Week 4, Week 8, and Week 13.
3. Competition Hierarchy:
   - Secondary Objective (Tier 2 / Priority B): Championnat Régional d'Aviron Indoor in Manosque on December 6, 2026 (end of Week 13). Tactical and operational dress rehearsal for Paris under true race conditions with a 48–72h active micro-taper (train-through approach preserving winter base; never do a 2-week drop).
   - Primary Objective (Tier 1 / Priority A): Championnat de France d'Aviron Indoor in Paris in late January 2027 (~Week 20). Winter championship peak target with a full 10–14 day exponential taper (-40% to -50% volume drop).
4. Gospel Warmup & Ground Mobility Architecture:
   - Always preserve the canonical routines from Weeks 2-4: Routine de Mobilité au Sol (5 min, 7 exercises with tutorial links), Protocole A (2000m), Protocole B (4000m with 105% 2kW sprints), Protocole C (4000m with 90% 2kW sprints for AT priming without premature lactate acidosis), and Circuit Renforcement (4 rounds, 45s effort / 15s transition with video links).
5. For athlete queries or calculations:
   - Use `lookup_athlete` to retrieve active benchmarks, drag factor, HR max, Zone 1 ceiling, and cross-training adjustments.
   - Use `calculate_athlete_pacing` to compute complete pacing tables and Wolverine Level 4 stroke rate grids (@16 to @28).
   - When prescribing UT2, UT1, or AT workouts, use `align_session_pace_to_l4` to lock the intensity to the zone midpoint and closest L4 stroke rate, providing the split reminder in parentheses (e.g. `allure L4 @20 (2:31.7)`).
6. For weekly squad programming:
   - Generate French squad fiches matching the official CASC format with `generate_weekly_sheet`.
   - Generate English technical methodology notes with `generate_coach_notes`.
7. For external water/weather conditions, regatta dates (Ligue Sud PACA, FFAviron), or boat rigging specifications, delegate to `rowing_researcher`.
8. For Intervals.icu operations:
   - Always check Coach David's readiness via `intervals_get_athlete_wellness`.
   - When staging a session on the calendar, use `intervals_schedule_workout`. Show the full workout description and date before execution.
   - Note that Intervals.icu operations require human confirmation before running.
9. Communication style: Clear, encouraging, rigorous exercise science, bilingual (French for rower cards and fiches; English for coach analysis).""",
    tools=tools,
    before_agent_callback=_init_stage,
    after_tool_callback=_track_stage,
    after_agent_callback=_stage_after_agent,
)
