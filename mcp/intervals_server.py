"""Intervals.icu MCP server (FastMCP, stdio).

Provides tools to inspect athlete readiness and schedule structured workouts on Intervals.icu.
Operates in DRY_RUN mode by default to ensure safe offline testing, and executes live
REST calls when valid API credentials are supplied.

Tools:
    get_athlete_wellness(date_str) -> Returns wellness, HRV, resting HR, fatigue (ATL/CTL).
    schedule_workout(workout_date, title, description, duration_minutes, workout_type) -> Schedules event on calendar.

Env:
    INTERVALS_API_KEY      Personal API key from Intervals.icu -> Settings -> Developer settings.
    INTERVALS_ATHLETE_ID   Intervals athlete ID (default "0" for authenticated account).
    DRY_RUN                "true" (default): log payload and simulate response.
                           "false": execute live HTTP calls to intervals.icu.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Any, Dict, Optional

import httpx
from fastmcp import FastMCP

API_BASE = "https://intervals.icu/api/v1"

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[intervals-mcp] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

mcp = FastMCP("intervals")


def _is_dry_run() -> bool:
    api_key = os.environ.get("INTERVALS_API_KEY", "").strip()
    if not api_key:
        return True
    return os.environ.get("DRY_RUN", "true").lower() != "false"


def _athlete_id() -> str:
    return os.environ.get("INTERVALS_ATHLETE_ID", "0").strip() or "0"


def _auth_headers() -> Dict[str, str]:
    api_key = os.environ.get("INTERVALS_API_KEY", "").strip()
    import base64
    user_pass = f"API_KEY:{api_key}".encode("utf-8")
    basic = base64.b64encode(user_pass).decode("utf-8")
    return {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


@mcp.tool
def get_athlete_wellness(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves readiness, HRV (rMSSD), resting heart rate, and training load (CTL/ATL/TSB) for the coach.
    
    Args:
        date_str: Optional ISO date string 'YYYY-MM-DD'. If omitted, returns today's wellness.
    """
    if _is_dry_run():
        target_date = date_str or "today"
        log.info("DRY_RUN get_athlete_wellness for date=%s", target_date)
        return {
            "status": "success",
            "dry_run": True,
            "athlete_id": _athlete_id(),
            "date": target_date,
            "resting_hr": 52,
            "hrv_rmssd": 64.5,
            "sleep_quality": 4,  # out of 5
            "soreness": 2,       # out of 5
            "fatigue": 2,        # out of 5
            "readiness": "Green / Optimal for High Intensity",
            "training_load": {
                "fitness_ctl": 58.4,
                "fatigue_atl": 62.1,
                "form_tsb": -3.7,
                "ramp_rate": 4.2,
            },
            "coach_guidance": "Athlete has assimilated prior volume well. High HRV and resting HR normal. Cleared for Priority 1 AT / AN sessions.",
        }

    aid = _athlete_id()
    endpoint = f"{API_BASE}/athlete/{aid}/wellness"
    if date_str:
        endpoint += f"/{date_str}"

    try:
        resp = httpx.get(endpoint, headers=_auth_headers(), timeout=30.0)
        resp.raise_for_status()
        return {"status": "success", "dry_run": False, "data": resp.json()}
    except Exception as e:
        log.error("Failed to query Intervals.icu wellness: %s", e)
        return {"status": "error", "message": str(e)}


@mcp.tool
def schedule_workout(
    workout_date: str,
    title: str,
    description: str,
    duration_minutes: int = 60,
    workout_type: str = "Rowing",
) -> Dict[str, Any]:
    """Schedules a structured workout session on the Intervals.icu calendar for Coach David Montgamery.
    
    Args:
        workout_date: Target date in 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS' format.
        title: Workout title (e.g. 'P1 - Dutney AT 3x7 min (1:00 rest)').
        description: Full structured session description (Warmup, main set paces, rest intervals, cool-down).
        duration_minutes: Estimated total workout duration in minutes.
        workout_type: Activity type (default 'Rowing').
    """
    # Normalize ISO date
    if "T" not in workout_date:
        start_date = f"{workout_date}T08:30:00"
    else:
        start_date = workout_date

    payload = {
        "category": "WORKOUT",
        "start_date_local": start_date,
        "name": title,
        "description": description,
        "type": workout_type,
        "moving_time": duration_minutes * 60,
    }

    if _is_dry_run():
        simulated_id = f"dryrun-{uuid.uuid4().hex[:10]}"
        log.info(
            "DRY_RUN schedule_workout created: id=%s date=%s title=%r",
            simulated_id,
            start_date,
            title,
        )
        return {
            "status": "success",
            "dry_run": True,
            "message": f"Successfully staged workout on Intervals.icu calendar (DRY RUN)",
            "event": {
                "id": simulated_id,
                "athlete_id": _athlete_id(),
                "start_date_local": start_date,
                "name": title,
                "type": workout_type,
                "duration_minutes": duration_minutes,
                "description_preview": description[:150] + ("..." if len(description) > 150 else ""),
                "calendar_url": f"https://intervals.icu/activities?date={workout_date.split('T')[0]}",
            },
        }

    aid = _athlete_id()
    endpoint = f"{API_BASE}/athlete/{aid}/events"
    try:
        resp = httpx.post(endpoint, headers=_auth_headers(), json=payload, timeout=30.0)
        resp.raise_for_status()
        result = resp.json()
        return {
            "status": "success",
            "dry_run": False,
            "message": "Workout successfully posted to Intervals.icu calendar",
            "event": result,
        }
    except Exception as e:
        log.error("Failed to post workout to Intervals.icu: %s", e)
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    mcp.run()
