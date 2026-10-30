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
