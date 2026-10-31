"""Unit tests for Model Armor screening and Cloud DLP guardrails in Rowing Coach."""

import os
from unittest.mock import MagicMock, patch

import pytest
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from rowing_coach.guardrails import (
    REFUSAL,
    FallbackGemini,
    content_config,
    friendly_block,
    model,
    redact_boundary,
    redact_pii,
)


def test_friendly_block_on_model_armor():
    """Verify that a MODEL_ARMOR block returns the standard squad refusal text."""
    blocked_resp = LlmResponse(
        content=None,
        error_code="MODEL_ARMOR",
        error_message="Prompt flagged by content security",
    )
    ctx = MagicMock(spec=CallbackContext)
    result = friendly_block(ctx, blocked_resp)
    assert result is not None
    assert result.content.parts[0].text == REFUSAL
    assert "rowing squad safety policy" in result.content.parts[0].text


def test_friendly_block_on_normal_response():
    """Verify that normal responses pass through untouched."""
    normal_resp = LlmResponse(
        content=genai_types.Content(
            role="model", parts=[genai_types.Part.from_text(text="Session prescribed: 4x2000m UT2.")]
        ),
        error_code=None,
    )
    ctx = MagicMock(spec=CallbackContext)
    result = friendly_block(ctx, normal_resp)
    assert result is None


def test_content_config_off():
    """When MODEL_ARMOR_TEMPLATE_ID is unset or off, model_armor_config is None."""
    with patch.dict(os.environ, {"MODEL_ARMOR_TEMPLATE_ID": "off"}):
        cfg = content_config()
        # Retries are configured, but model_armor is None
        assert cfg.model_armor_config is None


def test_model_builder_returns_fallback_gemini():
    """Verify model factory creates FallbackGemini with retry options and fallback cascade."""
    m = model("gemini-2.5-flash")
    assert isinstance(m, FallbackGemini)
    assert m.model == "gemini-2.5-flash"
    assert len(m.fallback_models) >= 2
    assert m.retry_options is not None


def test_dlp_boundary_mock():
    """Verify boundary redaction interface and cache."""
    with patch("rowing_coach.guardrails._deidentify", side_effect=lambda txt, it, lik: txt.replace("06 12 34 56 78", "[PHONE_NUMBER]")):
        redacted = redact_boundary("Call Coach David at 06 12 34 56 78")
        assert "[PHONE_NUMBER]" in redacted
        assert "06 12 34 56 78" not in redacted


def test_deterministic_sanitizer_function_call_args():
    """Verify that friendly_block deterministically rewrites raw athlete IDs in tool calls to pseudonym tokens."""
    fn_call = genai_types.Part.from_function_call(
        name="align_session_pace_to_l4",
        args={"athlete_id": "david_montgamery", "intensity_zone": "UT2"},
    )
    llm_resp = LlmResponse(
        content=genai_types.Content(role="model", parts=[fn_call]),
        error_code=None,
    )
    ctx = MagicMock(spec=CallbackContext)
    result = friendly_block(ctx, llm_resp)

    # Must be modified and return non-None with tokenized args
    assert result is not None
    assert result.content.parts[0].function_call.args["athlete_id"] == "[ATHLETE_ath_5a77195e]"
    assert result.content.parts[0].function_call.args["intensity_zone"] == "UT2"
    # Also verify in-place mutation
    assert llm_resp.content.parts[0].function_call.args["athlete_id"] == "[ATHLETE_ath_5a77195e]"


def test_deterministic_sanitizer_parenthetical_cleartext_text():
    """Verify that friendly_block deterministically strips parenthetical cleartext names from text output."""
    raw_text = (
        "For [ATHLETE_ath_5a77195e] (Coach David), a UT2 Base Session should be targeted at L4 @17 (2:25.3). "
        "This aligns with the midpoint of the UT2 zone (55% of 2kW)."
    )
    text_part = genai_types.Part.from_text(text=raw_text)
    llm_resp = LlmResponse(
        content=genai_types.Content(role="model", parts=[text_part]),
        error_code=None,
    )
    ctx = MagicMock(spec=CallbackContext)
    result = friendly_block(ctx, llm_resp)

    assert result is not None
    sanitized_text = result.content.parts[0].text
    assert "(Coach David)" not in sanitized_text
    assert "Coach David" not in sanitized_text
    assert "David" not in sanitized_text
    assert "[ATHLETE_ath_5a77195e], a UT2 Base Session should be targeted at L4 @17 (2:25.3)." in sanitized_text


def test_deterministic_sanitizer_squad_names_in_text():
    """Verify that friendly_block deterministically replaces squad names with tokens."""
    raw_text = "Alex Martin and Sophie Dubois are training with David Montgamery today."
    text_part = genai_types.Part.from_text(text=raw_text)
    llm_resp = LlmResponse(
        content=genai_types.Content(role="model", parts=[text_part]),
        error_code=None,
    )
    ctx = MagicMock(spec=CallbackContext)
    result = friendly_block(ctx, llm_resp)

    assert result is not None
    sanitized_text = result.content.parts[0].text
    assert "Alex Martin" not in sanitized_text
    assert "Sophie Dubois" not in sanitized_text
    assert "David Montgamery" not in sanitized_text
    assert "[ATHLETE_ath_db95573a]" in sanitized_text
    assert "[ATHLETE_ath_8d369eef]" in sanitized_text
    assert "[ATHLETE_ath_5a77195e]" in sanitized_text


def test_agent_before_tool_callback_sanitizes_args():
    """Verify that _sanitize_tool_args callback on root_agent mutates raw IDs in tool args before execution."""
    from rowing_coach.agent import _sanitize_tool_args

    tool_mock = MagicMock()
    tool_mock.name = "align_session_pace_to_l4"
    args = {"athlete_id": "david_montgamery", "intensity_zone": "UT2"}
    ctx_mock = MagicMock()

    ret = _sanitize_tool_args(tool_mock, args, ctx_mock)
    assert ret is None  # standard callback pattern returns None so tool continues
    assert args["athlete_id"] == "[ATHLETE_ath_5a77195e]"
    assert args["intensity_zone"] == "UT2"

