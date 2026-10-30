"""Multi-Region Inference Router for Rowing Coach Agent.

Implements Architectural Blueprint v0.2:
  - Dynamic fallback cascading across:
      global -> europe-west1 -> europe-west9 -> us-central1
  - Traps HTTP 429 RESOURCE_EXHAUSTED and service unavailability
  - Injects mandatory token preservation prompt instructions
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

DEFAULT_REGION_CASCADE = ["global", "europe-west1", "europe-west9", "us-central1"]
TOKEN_PRESERVATION_INSTRUCTION = (
    "Token Preservation Invariant: Be concise, direct, and factual. "
    "Preserve cached context tokens by avoiding preamble and conversational filler."
)


class MultiRegionInferenceRouter:
    """Cascading router that switches regions when HTTP 429 quota exhaustion is detected."""

    def __init__(
        self,
        regions: Optional[List[str]] = None,
        initial_region: Optional[str] = None,
    ):
        self.regions = regions or list(DEFAULT_REGION_CASCADE)
        self.current_index = 0
        if initial_region and initial_region in self.regions:
            self.current_index = self.regions.index(initial_region)
        self.failure_counts: Dict[str, int] = {r: 0 for r in self.regions}

    @property
    def current_region(self) -> str:
        return self.regions[self.current_index]

    def record_success(self, region: str) -> None:
        """Resets consecutive failure count on healthy response."""
        self.failure_counts[region] = 0

    def failover(self, error: Exception) -> str:
        """Advances to next region in cascade upon encountering HTTP 429 or quota errors."""
        failed_region = self.current_region
        self.failure_counts[failed_region] += 1
        self.current_index = (self.current_index + 1) % len(self.regions)
        next_region = self.current_region
        log.warning(
            "Inference Router: Region %s failed (%s). Triggering failover to %s.",
            failed_region,
            error,
            next_region,
        )
        return next_region

    async def execute_with_failover(
        self,
        call_fn: Callable[[str], Any],
        *args,
        **kwargs,
    ) -> Tuple[Any, str]:
        """Executes call_fn(region, *args, **kwargs) with automated regional failover.

        Returns (result, region_used).
        """
        attempts = 0
        max_attempts = len(self.regions)
        last_error = None

        while attempts < max_attempts:
            region = self.current_region
            try:
                result = await call_fn(region, *args, **kwargs)
                self.record_success(region)
                return result, region
            except Exception as e:
                err_str = str(e).lower()
                is_quota_exhausted = (
                    "429" in err_str
                    or "resource_exhausted" in err_str
                    or "quota" in err_str
                    or "503" in err_str
                )
                if is_quota_exhausted:
                    last_error = e
                    self.failover(e)
                    attempts += 1
                else:
                    # Non-transient/non-quota error: re-raise immediately
                    raise e

        raise RuntimeError(
            f"All regions in cascade exhausted ({self.regions}). Last error: {last_error}"
        ) from last_error
