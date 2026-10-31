"""Adversarial Challenge & Stress-Test Harness for Milestone M1.

Empirical verification of:
1. ReversiblePiiVault:
   - Complex names, coach aliases, accents, mixed PII (email, phones).
   - Subword boundary protection and possessives.
   - Prompt-only rehydration.
   - Squad reverse map rehydration for all 16 squad tokens without prompt PII.
   - Strict fail-closed invariant on adversarial/unknown tokens (VaultSafetyError).
2. local_proxy.stream_agent_pipeline:
   - Ingress payload verification (vault tokens preserved, zero unmasking to snake_case IDs or raw names).
   - Gate 2 graceful safety error handling when orphan tokens appear in output.
3. Tool Output GDPR Article 9 Pseudonymization & Zero Leakage:
   - lookup_athlete, calculate_athlete_pacing, align_session_pace_to_l4.
   - Error messages on unknown queries never leak squad rosters.
"""

import json
import re
from unittest.mock import patch
import pytest

from backend.pii_vault import ReversiblePiiVault, VaultSafetyError
import local_proxy
from rowing_coach.tools import (
    lookup_athlete,
    get_squad_roster,
    calculate_athlete_pacing,
    align_session_pace_to_l4,
)


# ==============================================================================
# 1. ReversiblePiiVault Adversarial Stress Tests
# ==============================================================================

def test_vault_complex_names_and_coach_aliases():
    """Empirically test vaulting across coach aliases, accented names, and mixed PII."""
    vault = ReversiblePiiVault()
    session_id = "stress_test_sess_01"

    # 1. Coach aliases test
    aliases = [
        "Coach David",
        "david montgomery",
        "David Montgamery",
        "coach david",
        "DAVID MONTGOMERY",
    ]
    for idx, alias in enumerate(aliases):
        prompt = f"Plan session for {alias} today."
        vaulted = vault.strip_and_vault(prompt, f"{session_id}_alias_{idx}")
        assert "[ATHLETE_ath_5a77195e]" in vaulted, f"Failed to vault alias: {alias}"
        assert alias not in vaulted, f"Alias leaked in vaulted string: {alias}"

    # 2. Accented and unaccented names
    accents = [
        ("Valérie Caron", "ath_ae26b87e"),
        ("Valerie Caron", "ath_ae26b87e"),
        ("Céline Brun", "ath_90ffaf04"),
        ("Celine Brun", "ath_90ffaf04"),
    ]
    for name, expected_pseudo in accents:
        prompt = f"Check 2k watts for {name}."
        vaulted = vault.strip_and_vault(prompt, f"{session_id}_{expected_pseudo}")
        expected_token = f"[ATHLETE_{expected_pseudo}]"
        assert expected_token in vaulted, f"Failed for {name}: expected {expected_token} in {vaulted}"
        assert name not in vaulted, f"Raw name leaked in vaulted string: {name}"

    # 3. Mixed multi-PII prompt (athletes, email, domestic phone)
    mixed_prompt = (
        "Coach David called regarding Alex Martin and Sophie Dubois. "
        "Reach out at coach.david@rowing.org or 06 12 34 56 78 / 020 7946 0123."
    )
    vaulted_mixed = vault.strip_and_vault(mixed_prompt, f"{session_id}_mixed")
    assert "[ATHLETE_ath_5a77195e]" in vaulted_mixed
    assert "[ATHLETE_ath_db95573a]" in vaulted_mixed
    assert "[ATHLETE_ath_8d369eef]" in vaulted_mixed
    assert "[EMAIL_ADDRESS_1]" in vaulted_mixed
    assert "[PHONE_NUMBER_1]" in vaulted_mixed or "[PHONE_NUMBER_2]" in vaulted_mixed
    assert "Coach David" not in vaulted_mixed
    assert "Alex Martin" not in vaulted_mixed
    assert "Sophie Dubois" not in vaulted_mixed
    assert "coach.david@rowing.org" not in vaulted_mixed
    assert "06 12 34 56 78" not in vaulted_mixed


def test_vault_subword_and_possessive_boundaries():
    """Verify word boundaries prevent substring corruption (e.g. Alexander != Alex) and handle possessives."""
    vault = ReversiblePiiVault()
    session_id = "stress_test_boundary"

    # Alexander contains 'Alex', but should NOT be vaulted as Alex Martin
    prompt = "Alexander is rowing alongside Alex Martin today."
    vaulted = vault.strip_and_vault(prompt, session_id)
    assert "Alexander is rowing alongside [ATHLETE_ath_db95573a] today." == vaulted

    # Possessives
    prompt_possessive = "What is Coach David's 2k split and Alex Martin's drag factor?"
    vaulted_possessive = vault.strip_and_vault(prompt_possessive, f"{session_id}_poss")
    assert "[ATHLETE_ath_5a77195e]'s" in vaulted_possessive
    assert "[ATHLETE_ath_db95573a]'s" in vaulted_possessive


def test_rehydration_with_prompt_tokens_only():
    """Verify rehydration reproduces prompt tokens faithfully."""
    vault = ReversiblePiiVault()
    session_id = "stress_test_rehydrate_prompt"

    prompt = "Pacing plan for Coach David and Alex Martin."
    vaulted = vault.strip_and_vault(prompt, session_id)

    # Model generates response using prompt tokens
    model_reply = (
        "Pacing targets:\n"
        "- [ATHLETE_ath_5a77195e]: 1:58.2 / 500m (UT2 @ 2:25.3)\n"
        "- [ATHLETE_ath_db95573a]: 2:08.4 / 500m (UT2 @ 2:37.8)"
    )

    rehydrated = vault.rehydrate(model_reply, session_id)
    assert "Coach David: 1:58.2 / 500m" in rehydrated
    assert "Alex Martin: 2:08.4 / 500m" in rehydrated
    assert "[ATHLETE_" not in rehydrated


def test_rehydration_all_16_squad_tokens_without_prompt_pii():
    """Empirically test that ALL 16 squad tokens rehydrate to friendly names without prompt PII."""
    vault = ReversiblePiiVault()
    session_id = "stress_test_all_16_squad"

    # Impersonal prompt containing NO athlete names
    impersonal_prompt = "Generate the full squad roster with current benchmarks."
    vaulted = vault.strip_and_vault(impersonal_prompt, session_id)
    assert vaulted == impersonal_prompt

    # Query get_squad_roster to get all tokens
    roster_res = get_squad_roster()
    assert roster_res["status"] == "success"
    all_tokens = [a["id"] for a in roster_res["squad_roster"]]
    assert len(all_tokens) == 16

    # Build simulated LLM output containing every single squad token
    llm_output_lines = [f"- Token {tok} benchmark split recorded." for tok in all_tokens]
    llm_output = "\n".join(llm_output_lines)

    # Rehydrate
    rehydrated = vault.rehydrate(llm_output, session_id)

    # Assert zero orphan tokens remain and no VaultSafetyError was raised
    assert "[ATHLETE_" not in rehydrated
    assert "Coach David" in rehydrated
    assert "Alex Martin" in rehydrated
    assert "Sophie Dubois" in rehydrated
    assert "Florence Moreau" in rehydrated
    assert "Lucas Bernard" in rehydrated
    assert "Marie Laurent" in rehydrated
    assert "Pierre Simon" in rehydrated
    assert "Claire Fontaine" in rehydrated
    assert "Guillaume Mercier" in rehydrated
    assert "Brigitte Roux" in rehydrated
    assert "Marc Leroy" in rehydrated
    assert "Philippe Girard" in rehydrated
    assert "Romain Bonnet" in rehydrated
    assert "Valérie Caron" in rehydrated or "Valerie Caron" in rehydrated
    assert "Sandrine Roche" in rehydrated
    assert "Céline Brun" in rehydrated or "Celine Brun" in rehydrated


def test_rehydration_adversarial_unknown_tokens_strictly_fails_closed():
    """Empirically verify that adversarial or hallucinated lowercase hex tokens strictly raise VaultSafetyError."""
    vault = ReversiblePiiVault()
    session_id = "stress_test_fail_closed"

    adversarial_cases = [
        "Session allocated to [ATHLETE_ath_deadbeef].",
        "Target split for [ATHLETE_NAME_999] is 2:00.0.",
        "Contact [PHONE_NUMBER_99] for details.",
        "Send logs to [EMAIL_ADDRESS_42].",
        "Assigned to [ATHLETE_ath_00000000] and [ATHLETE_ath_c0ffee].",
        "Mixed valid and invalid: [ATHLETE_ath_5a77195e] and [ATHLETE_ath_deadbeef].",
    ]

    for case in adversarial_cases:
        with pytest.raises(VaultSafetyError) as exc_info:
            vault.rehydrate(case, session_id)
        assert "Gate 2 Safety Violation: Detected orphan or hallucinated PII tokens" in str(exc_info.value)


# ==============================================================================
# 2. local_proxy.stream_agent_pipeline Stress Tests
# ==============================================================================

@pytest.mark.anyio
async def test_stream_pipeline_ingress_dispatch_never_unmasks_tokens():
    """Empirically verify payload['input']['message'] retains vault tokens and NEVER leaks raw database keys or names."""
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
                    "partial": True,
                    "content": {
                        "parts": [{"text": "Pacing profile for [ATHLETE_ath_5a77195e] and [ATHLETE_ath_db95573a]."}]
                    }
                }
            }
            yield json.dumps(chunk)

    def mock_stream(method, url, headers=None, json=None):
        nonlocal captured_payload
        captured_payload = json
        return MockStreamContext(200)

    test_prompt = "Please analyze pacing for Coach David, Alex Martin, and Sophie Dubois."

    with patch("httpx.AsyncClient.stream", side_effect=mock_stream):
        events = []
        async for evt in local_proxy.stream_agent_pipeline(
            raw_message=test_prompt,
            target="local",
        ):
            events.append(evt)

        assert captured_payload is not None, "Payload was not captured by mock stream"
        dispatched_message = captured_payload["input"]["message"]

        # Tokens MUST be present
        assert "[ATHLETE_ath_5a77195e]" in dispatched_message
        assert "[ATHLETE_ath_db95573a]" in dispatched_message
        assert "[ATHLETE_ath_8d369eef]" in dispatched_message

        # Raw database keys MUST NEVER appear in dispatched payload
        assert "david_montgamery" not in dispatched_message
        assert "alex_martin" not in dispatched_message
        assert "sophie_dubois" not in dispatched_message

        # Cleartext athlete names MUST NEVER appear in dispatched payload
        assert "Coach David" not in dispatched_message
        assert "Alex Martin" not in dispatched_message
        assert "Sophie Dubois" not in dispatched_message

        # Gate 2 rehydration completed cleanly
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        assert "Coach David" in done_events[0]["rehydrated"]
        assert "Alex Martin" in done_events[0]["rehydrated"]


@pytest.mark.anyio
async def test_stream_pipeline_gate_2_catches_safety_error_gracefully():
    """Empirically verify that Gate 2 catches VaultSafetyError without crashing or raising 500."""
    class MockHallucinatedStreamContext:
        def __init__(self, status_code=200):
            self.status_code = status_code

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def aiter_lines(self):
            chunk = {
                "output": {
                    "partial": True,
                    "content": {
                        "parts": [{"text": "Unauthorized plan created for [ATHLETE_ath_deadbeef]."}]
                    }
                }
            }
            yield json.dumps(chunk)

    def mock_stream(method, url, headers=None, json=None):
        return MockHallucinatedStreamContext(200)

    with patch("httpx.AsyncClient.stream", side_effect=mock_stream):
        events = []
        # Pipeline must NOT throw an unhandled exception
        async for evt in local_proxy.stream_agent_pipeline(
            raw_message="Run test plan.",
            target="local",
        ):
            events.append(evt)

        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1
        rehydrated = done_events[0]["rehydrated"]

        # Assert safe error banner was generated instead of leaking orphan or crashing
        assert "[Gate 2 Safety Error]" in rehydrated
        assert "Gate 2 Safety Violation: Detected orphan or hallucinated PII tokens" in rehydrated
        assert "[ATHLETE_ath_deadbeef]" in rehydrated


# ==============================================================================
# 3. Tool GDPR Article 9 Pseudonymization & Leakage Resistance
# ==============================================================================

def test_lookup_athlete_unknown_query_zero_roster_leak():
    """Empirically test that lookup_athlete on unknown query does NOT leak real athlete names."""
    res = lookup_athlete("completely_unknown_rower_12345")
    assert res["status"] == "not_found"
    msg = res["message"]
    # Ensure real athlete names do not appear in the error message
    assert "Alex Martin" not in msg
    assert "David Montgamery" not in msg
    assert "Sophie Dubois" not in msg
    assert "david_montgamery" not in msg


def test_tool_outputs_adhere_to_pseudonym_tokens():
    """Verify pacing and L4 alignment tools strictly return pseudonym tokens."""
    # Test calculate_athlete_pacing
    pacing_david = calculate_athlete_pacing("[ATHLETE_ath_5a77195e]")
    assert pacing_david["status"] == "success"
    assert pacing_david["athlete_id"] == "[ATHLETE_ath_5a77195e]"
    assert pacing_david["name"] == "[ATHLETE_ath_5a77195e]"
    assert "david_montgamery" not in str(pacing_david)
    assert "David Montgamery" not in str(pacing_david)

    # Test align_session_pace_to_l4
    align_alex = align_session_pace_to_l4("[ATHLETE_ath_db95573a]", "UT2")
    assert align_alex["status"] == "success"
    assert align_alex["athlete_id"] == "[ATHLETE_ath_db95573a]"
    assert align_alex["name"] == "[ATHLETE_ath_db95573a]"
    assert "Alex Martin" not in str(align_alex)
    assert "alex_martin" not in str(align_alex)
