"""Integration tests for Rowing Coach ADK agent structure and configuration."""

import pytest
from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.skill_toolset import SkillToolset
from rowing_coach.agent import intervals_toolset, research_agent, root_agent, skill_toolset


def test_root_agent_structure():
    assert isinstance(root_agent, Agent)
    assert root_agent.name == "rowing_coach"
    assert "evidence-based" in root_agent.instruction.lower()
    assert "masters-rowing-rules" in root_agent.instruction


def test_tools_composition():
    tool_names = [getattr(t, "name", None) or getattr(t, "__name__", None) or type(t).__name__ for t in root_agent.tools]
    
    # Custom function tools
    assert "get_season_schedule" in tool_names
    assert "lookup_athlete" in tool_names
    assert "calculate_athlete_pacing" in tool_names
    assert "align_session_pace_to_l4" in tool_names
    assert "generate_weekly_sheet" in tool_names
    assert "generate_coach_notes" in tool_names

    # SkillToolset
    assert any(isinstance(t, SkillToolset) for t in root_agent.tools)

    # Sub-agent via AgentTool
    assert any(isinstance(t, AgentTool) and t.agent.name == "rowing_researcher" for t in root_agent.tools)

    # Intervals McpToolset
    assert any(isinstance(t, McpToolset) for t in root_agent.tools)


def test_subagent_researcher():
    assert isinstance(research_agent, Agent)
    assert research_agent.name == "rowing_researcher"
    assert "google_search" in [t.name if hasattr(t, "name") else str(t) for t in research_agent.tools]
    assert "Base Nautique du Lac" in research_agent.instruction
    assert "avironsudpaca" in research_agent.instruction


def test_intervals_mcp_confirmation_gate():
    assert isinstance(intervals_toolset, McpToolset)
    assert intervals_toolset.require_confirmation is True
    assert intervals_toolset.tool_name_prefix == "intervals"
    assert "get_athlete_wellness" in intervals_toolset.tool_filter
    assert "schedule_workout" in intervals_toolset.tool_filter
