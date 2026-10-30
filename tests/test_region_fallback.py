"""Test Multi-Region Inference Router and HTTP 429 Failover (Blueprint v0.2)."""

import pytest
from backend.llm_router import MultiRegionInferenceRouter


@pytest.mark.anyio
async def test_router_cascades_on_429():
    router = MultiRegionInferenceRouter(
        regions=["global", "europe-west1", "europe-west9", "us-central1"]
    )
    assert router.current_region == "global"

    attempts = []

    async def mock_call(region: str):
        attempts.append(region)
        if region in ("global", "europe-west1"):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota limit exceeded")
        return "workout_generated"

    result, used_region = await router.execute_with_failover(mock_call)

    assert result == "workout_generated"
    assert used_region == "europe-west9"
    assert attempts == ["global", "europe-west1", "europe-west9"]


@pytest.mark.anyio
async def test_router_raises_on_non_quota_error():
    router = MultiRegionInferenceRouter()

    async def mock_call(region: str):
        raise ValueError("Invalid athlete ID provided")

    with pytest.raises(ValueError, match="Invalid athlete ID"):
        await router.execute_with_failover(mock_call)
