"""Unit tests for Governance CLI in Rowing Coach."""

import os
from unittest.mock import MagicMock, patch
import pytest

from governance import resolve_project, resolve_region


def test_resolve_project_explicit_flag():
    """Tier 1: Explicit CLI flag takes absolute precedence."""
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-proj"}):
        p = resolve_project("cli-proj")
        assert p == "cli-proj"


def test_resolve_project_env_var():
    """Tier 2: Environment variable resolved if no flag provided."""
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "env-proj"}):
        p = resolve_project(None)
        assert p == "env-proj"


def test_resolve_project_adc_fallback():
    """Tier 4: ADC fallback if env vars missing."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("google.auth.default", return_value=(MagicMock(), "adc-proj")):
            p = resolve_project(None)
            assert p == "adc-proj"


def test_resolve_region_default():
    """Default region is us-central1 if unspecified."""
    with patch.dict(os.environ, {}, clear=True):
        assert resolve_region(None) == "us-central1"
        assert resolve_region("europe-west1") == "europe-west1"
