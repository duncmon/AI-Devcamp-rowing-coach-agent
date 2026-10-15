"""Configure custom Memory Bank topics and TTL for the deployed Rowing Coach engine.

Usage:
  uv run python memory_bank_config.py show
  uv run python memory_bank_config.py apply
"""

import json
import os
import sys

import google.auth
import google.auth.transport.requests
import httpx

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or sys.exit(
    "Set GOOGLE_CLOUD_PROJECT to your GCP project ID (export GOOGLE_CLOUD_PROJECT=...)."
)
REGION = os.environ.get("ENGINE_REGION", "us-central1")
ENGINE = os.environ.get("AGENT_ENGINE_ID") or sys.exit(
    "Set AGENT_ENGINE_ID to your deployed engine's numeric ID (see `agents-cli deploy --status`)."
)
URL = (
    f"https://{REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}"
    f"/locations/{REGION}/reasoningEngines/{ENGINE}"
)

CONFIG = {
    "customizationConfigs": [
        {
            "memoryTopics": [
                {"managedMemoryTopic": {"managedTopicEnum": "USER_PREFERENCES"}},
                {"managedMemoryTopic": {"managedTopicEnum": "EXPLICIT_INSTRUCTIONS"}},
                {
                    "customMemoryTopic": {
                        "label": "athlete_constraints_and_history",
                        "description": (
                            "Athlete medical flags, injuries, resting autonomic metrics, "
                            "cardiac and orthopedic restrictions, and individualized physiological history."
                        ),
                    }
                },
                {
                    "customMemoryTopic": {
                        "label": "plan_overrides_and_progressions",
                        "description": (
                            "Dynamic deviations from the standard season plan, such as custom "
                            "anaerobic block interval progressions, shifted deloads, and weekly session adjustments."
                        ),
                    }
                },
                {
                    "customMemoryTopic": {
                        "label": "coaching_preferences_and_style",
                        "description": (
                            "Coach David Montgamery's pedagogical tone, bilingual requirements "
                            "(French for athlete fiches, English for coaching methodology), and preferred formatting."
                        ),
                    }
                },
            ],
        }
    ],
    "ttlConfig": {"defaultTtl": "7776000s"},  # 90 days
}


def _headers():
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return {"Authorization": f"Bearer {creds.token}", "x-goog-user-project": PROJECT}


def show():
    r = httpx.get(URL, headers=_headers(), timeout=30)
    r.raise_for_status()
    print(json.dumps(r.json().get("contextSpec", {}), indent=1))


def apply():
    cur = httpx.get(URL, headers=_headers(), timeout=30).json()
    mb = cur.get("contextSpec", {}).get("memoryBankConfig", {})
    mb.update(CONFIG)
    r = httpx.patch(
        URL,
        headers=_headers(),
        timeout=60,
        params={"updateMask": "contextSpec.memoryBankConfig"},
        json={"contextSpec": {"memoryBankConfig": mb}},
    )
    if r.status_code >= 400:
        sys.exit(f"{r.status_code}: {r.text[:600]}")
    print("Memory Bank config applied successfully; operation:", r.json().get("name", "?"))


if __name__ == "__main__":
    {"show": show, "apply": apply}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__)
    )()
