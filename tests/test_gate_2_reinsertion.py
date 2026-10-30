"""Test Gate 2: Rehydration and Hallucination Assertion (Blueprint v0.2)."""

import pytest
from backend.pii_vault import ReversiblePiiVault, VaultSafetyError


def test_gate_2_clean_rehydration():
    vault = ReversiblePiiVault()
    session_id = "session_test_gate_2"

    raw_input = "Prescribe 5x1500m for Daniel Okonkwo."
    stripped = vault.strip_and_vault(raw_input, session_id)

    # Simulated LLM output using the token
    llm_recommendation = (
        "Weekly Plan: [ATHLETE_NAME_1] is scheduled for 5x1500m at L4 @20."
    )

    rehydrated = vault.rehydrate(llm_recommendation, session_id)

    assert "Daniel Okonkwo" in rehydrated
    assert "[ATHLETE_NAME_1]" not in rehydrated


def test_gate_2_blocks_orphan_tokens():
    """Gate 2 must fail closed if an unmapped / orphan token is detected."""
    vault = ReversiblePiiVault()
    session_id = "session_orphan_test"

    # LLM hallucinates an unknown token [ATHLETE_NAME_99]
    hallucinated_output = "Assign 3x2000m to [ATHLETE_NAME_99]."

    with pytest.raises(VaultSafetyError, match="Gate 2 Safety Violation"):
        vault.rehydrate(hallucinated_output, session_id)
