"""Test Cryptographic Athlete Pseudonym Resolution under GDPR Article 9."""

import pytest
from backend.pii_vault import ReversiblePiiVault, VaultSafetyError
from rowing_coach.tools import lookup_athlete, calculate_athlete_pacing, align_session_pace_to_l4


def test_cryptographic_athlete_vaulting():
    vault = ReversiblePiiVault()
    session_id = "test_crypto_vault_sess"

    prompt_david = "What is Coach David's UT2 Base Session?"
    vaulted_david = vault.strip_and_vault(prompt_david, session_id)

    # Assert Coach David is replaced with cryptographic pseudonym
    assert "Coach David" not in vaulted_david
    assert "[ATHLETE_ath_5a77195e]" in vaulted_david

    prompt_montgomery = "Calculate L4 grid for David Montgomery."
    vaulted_mont = vault.strip_and_vault(prompt_montgomery, session_id)
    assert "David Montgomery" not in vaulted_mont
    assert "[ATHLETE_ath_5a77195e]" in vaulted_mont

    prompt_alex = "What is Alex Martin's 2k split?"
    vaulted_alex = vault.strip_and_vault(prompt_alex, session_id)
    assert "Alex Martin" not in vaulted_alex
    assert "[ATHLETE_ath_db95573a]" in vaulted_alex


def test_cryptographic_rehydration():
    vault = ReversiblePiiVault()
    session_id = "test_crypto_rehydrate_sess"

    raw_input = "Please calculate pacing for Coach David."
    stripped = vault.strip_and_vault(raw_input, session_id)
    assert "[ATHLETE_ath_5a77195e]" in stripped

    # Simulated model output containing the token
    model_output = "Based on [ATHLETE_ath_5a77195e]'s 2k test of 1:58.2, his UT2 target is 2:25.3."
    rehydrated = vault.rehydrate(model_output, session_id)

    assert "Coach David" in rehydrated
    assert "[ATHLETE_ath_5a77195e]" not in rehydrated


def test_orphan_cryptographic_token_fails_closed():
    vault = ReversiblePiiVault()
    session_id = "test_orphan_crypto_sess"

    # Hallucinated / unmapped cryptographic token
    hallucinated = "Schedule workout for [ATHLETE_ath_deadbeef]."
    with pytest.raises(VaultSafetyError, match="Gate 2 Safety Violation"):
        vault.rehydrate(hallucinated, session_id)


def test_tools_resolve_cryptographic_pseudonyms():
    # Lookup by token
    res = lookup_athlete("[ATHLETE_ath_5a77195e]")
    assert res.get("status") == "success"
    assert res["athlete"]["id"] == "david_montgamery"
    assert res["athlete"]["benchmark_2k_split"] == "1:58.2"

    # Pacing by token
    pacing = calculate_athlete_pacing("[ATHLETE_ath_5a77195e]")
    assert pacing["status"] == "success"
    assert pacing["benchmark_2k_split"] == "1:58.2"
    assert "@17" in pacing["l4_table"]

    # Align session by token
    align = align_session_pace_to_l4("[ATHLETE_ath_5a77195e]", "UT2")
    assert align["status"] == "success"
    assert align["aligned_stroke_rate"] in (17, 20)
