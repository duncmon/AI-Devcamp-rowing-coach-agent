"""Test Gate 1: Stripping and Vaulting of Athlete PII (Blueprint v0.2)."""

import pytest
from backend.pii_vault import ReversiblePiiVault


def test_gate_1_strips_athlete_pii():
    vault = ReversiblePiiVault()
    session_id = "session_test_gate_1"

    raw_text = (
        "Coach, please update the erg program for Daniel Okonkwo. "
        "His contact number is 06 12 34 56 78 and email is daniel@example.com."
    )

    stripped = vault.strip_and_vault(raw_text, session_id)

    # Assert raw PII is stripped
    assert "Daniel Okonkwo" not in stripped
    assert "06 12 34 56 78" not in stripped
    assert "daniel@example.com" not in stripped

    # Assert tokens are present
    assert "[ATHLETE_NAME_1]" in stripped
    assert "[PHONE_NUMBER_1]" in stripped
    assert "[EMAIL_ADDRESS_1]" in stripped


def test_gate_1_preserves_consistent_tokens_across_turns():
    vault = ReversiblePiiVault()
    session_id = "session_multi_turn"

    turn_1 = "Athlete Daniel Okonkwo completed 3x7min AT."
    turn_2 = "Can Daniel Okonkwo do UT2 tomorrow?"

    s1 = vault.strip_and_vault(turn_1, session_id)
    s2 = vault.strip_and_vault(turn_2, session_id)

    assert "[ATHLETE_NAME_1]" in s1
    assert "[ATHLETE_NAME_1]" in s2
    assert "[ATHLETE_NAME_2]" not in s2  # Consistent token reuse
