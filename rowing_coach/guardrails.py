"""Guardrails for Rowing Coach Agent: Model Armor screening and Cloud DLP PII sanitization.

Layers:
  1. content_config()   Model Armor screening applied inline on each Gemini call
  2. friendly_block     after_model_callback: turns Model Armor blocks into a standard rowing squad refusal
  3. redact_boundary    Cloud DLP + custom regex scrubbing athlete PII at the ASGI HTTP boundary
  4. FallbackGemini     Cascading model failover with exponential backoff & jitter

Standard Rowing Squad Refusal:
  "This request was flagged by our rowing squad safety policy and cannot be processed."
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.cloud import dlp_v2
from google.genai import types as genai_types

log = logging.getLogger(__name__)

# Project ID resolution hierarchy: Environment -> ADC fallback
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DLP_PROJECT = os.environ.get("PROJECT_ID") or PROJECT
if not DLP_PROJECT or DLP_PROJECT.isdigit():
    try:
        import google.auth
        _, adc_project = google.auth.default()
        if adc_project and not adc_project.isdigit():
            DLP_PROJECT = adc_project
    except Exception:
        pass

# Template region: defaults to europe-west1 for EU sovereignty, with us-central1 fallback
MA_REGION = os.environ.get("MODEL_ARMOR_REGION", "europe-west1")
MA_TEMPLATE = os.environ.get("MODEL_ARMOR_TEMPLATE_ID", "")
if MA_TEMPLATE.strip().lower() in ("off", "none", "disabled"):
    MA_TEMPLATE = ""

# Align Vertex location if Model Armor is active
if MA_TEMPLATE and os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() in ("global", ""):
    log.warning(
        "Model Armor requires a regional location matching the template (%s). "
        "Overriding GOOGLE_CLOUD_LOCATION to %r.",
        MA_REGION,
        MA_REGION,
    )
    os.environ["GOOGLE_CLOUD_LOCATION"] = MA_REGION
    os.environ["VERTEX_LOCATION"] = MA_REGION

MA_OUTPUT_TEMPLATE = os.environ.get(
    "MODEL_ARMOR_OUTPUT_TEMPLATE_ID", f"{MA_TEMPLATE}-output" if MA_TEMPLATE else ""
)

PII_REDACTION = os.environ.get("PII_REDACTION", "off").strip().lower() in ("on", "true", "1")

REFUSAL = "This request was flagged by our rowing squad safety policy and cannot be processed."

_dlp_client = None


def _dlp() -> dlp_v2.DlpServiceClient:
    global _dlp_client
    if _dlp_client is None:
        _dlp_client = dlp_v2.DlpServiceClient()
    return _dlp_client


def _template(template_id: str) -> str:
    return f"projects/{DLP_PROJECT}/locations/{MA_REGION}/templates/{template_id}"


RETRY_CONFIG = genai_types.HttpRetryOptions(
    attempts=6,
    initial_delay=1.5,
    max_delay=32.0,
    exp_base=2.0,
    jitter=0.5,
    http_status_codes=[400, 408, 429, 500, 502, 503, 504],
)


def content_config(screen_input: bool = True) -> Optional[genai_types.GenerateContentConfig]:
    """Generates content config attaching Model Armor templates and HTTP retries."""
    model_armor = None
    if MA_TEMPLATE:
        if os.environ.get("GOOGLE_CLOUD_LOCATION", "global") == "global":
            log.error(
                "Model Armor is on but GOOGLE_CLOUD_LOCATION is global: Gemini calls "
                "will fail. Use a regional location."
            )
        model_armor = genai_types.ModelArmorConfig(
            prompt_template_name=_template(MA_TEMPLATE) if screen_input else None,
            response_template_name=_template(MA_OUTPUT_TEMPLATE) if MA_OUTPUT_TEMPLATE else None,
        )

    return genai_types.GenerateContentConfig(
        model_armor_config=model_armor,
        http_options=genai_types.HttpOptions(retry_options=RETRY_CONFIG),
    )


class FallbackGemini(Gemini):
    """Gemini model adapter with cascading fallback across versions and retries."""
    fallback_models: list[str] = []

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        models_to_try = [self.model] + [m for m in self.fallback_models if m != self.model]
        last_err = None
        for candidate in models_to_try:
            try:
                llm_request.model = candidate
                async for resp in super().generate_content_async(llm_request, stream=stream):
                    yield resp
                return
            except Exception as e:
                err_str = str(e)
                if "404" in err_str or "not found" in err_str.lower():
                    log.warning(
                        "Model %s returned 404 NOT_FOUND; falling back to next candidate...",
                        candidate,
                    )
                    last_err = e
                    continue
                raise e
        if last_err:
            raise last_err


def model(name: str) -> FallbackGemini:
    """Returns a resilient model adapter configured with role-based fallbacks and retries."""
    custom_fallbacks = os.environ.get("MODEL_FALLBACKS")
    if custom_fallbacks and custom_fallbacks.strip():
        fallbacks = [m.strip() for m in custom_fallbacks.split(",") if m.strip()]
    elif "flash-lite" in name.lower():
        fallbacks = ["gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-3.0-flash-lite"]
    elif "pro" in name.lower():
        fallbacks = ["gemini-2.5-pro", "gemini-3.1-pro-preview", "gemini-2.5-flash"]
    else:
        fallbacks = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.8-flash"]

    return FallbackGemini(
        model=name,
        fallback_models=fallbacks,
        retry_options=RETRY_CONFIG,
    )


def friendly_block(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """after_model_callback: converts a Model Armor block into the standard squad refusal."""
    if llm_response.error_code == "MODEL_ARMOR":
        log.warning("Model Armor blocked a call: %s", llm_response.error_message)
        return LlmResponse(
            content=genai_types.Content(
                role="model", parts=[genai_types.Part.from_text(text=REFUSAL)]
            )
        )
    return None


# --- Boundary & Storage DLP ---------------------------------------------------

_DLP_PASSES = [
    (["EMAIL_ADDRESS", "PHONE_NUMBER"], "POSSIBLE"),
    (["PERSON_NAME", "LOCATION"], "LIKELY"),
]

# Domestic & International phone regex (France, UK, etc.)
_PHONE_REGEX = (
    r"(?:"
    r"(?:\+\d{1,3}[\s-]?)?"
    r"(?:"
    r"0[1-9](?:[\s.-]?\d{2}){4}"                      # France (06 12 34 56 78 / 0492123456)
    r"|"
    r"0[1-9]\d{1,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}"      # UK (020 7946 0000)
    r"|"
    r"1[3-9]\d{9}"                                    # Mobile
    r")"
    r")"
)

_CUSTOM_PHONE_INFO_TYPE = [
    {
        "info_type": {"name": "PHONE_NUMBER"},
        "regex": {"pattern": _PHONE_REGEX},
        "likelihood": "LIKELY",
    }
]


def _deidentify(text: str, info_types: list[str], min_likelihood: str) -> str:
    inspect_config = {
        "info_types": [{"name": t} for t in info_types],
        "min_likelihood": min_likelihood,
    }
    if "PHONE_NUMBER" in info_types:
        inspect_config["custom_info_types"] = _CUSTOM_PHONE_INFO_TYPE

    resp = _dlp().deidentify_content(
        request={
            "parent": f"projects/{DLP_PROJECT}/locations/global",
            "inspect_config": inspect_config,
            "deidentify_config": {
                "info_type_transformations": {
                    "transformations": [
                        {"primitive_transformation": {"replace_with_info_type_config": {}}}
                    ]
                }
            },
            "item": {"value": text},
        },
        timeout=15,
    )
    return resp.item.value or text


_BOUNDARY_PASSES = [
    (["EMAIL_ADDRESS", "PHONE_NUMBER"], "POSSIBLE"),
    (["PERSON_NAME"], "LIKELY"),
]
_boundary_cache: dict[str, str] = {}


def redact_boundary(text: str) -> str:
    """De-identify a user's prompt before it reaches the agent, session store, or Memory Bank."""
    if not text or not text.strip():
        return text
    hit = _boundary_cache.get(text)
    if hit is not None:
        return hit
    out = text
    for info_types, likelihood in _BOUNDARY_PASSES:
        out = _deidentify(out, info_types, likelihood)
    if len(_boundary_cache) > 512:
        _boundary_cache.clear()
    _boundary_cache[text] = out
    return out


def redact_pii(text: str) -> str:
    """De-identifies full PII including locations before external sync/export."""
    for info_types, likelihood in _DLP_PASSES:
        text = _deidentify(text, info_types, likelihood)
    return text
