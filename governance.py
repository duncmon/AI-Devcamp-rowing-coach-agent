"""Governance Policies and Agent Registry CLI for Rowing Coach Agent.

Implements multi-tier fallback configuration hierarchy:
  1. Explicit CLI Flags (--project, --region)
  2. Process Environment (GOOGLE_CLOUD_PROJECT, PROJECT_ID)
  3. Local .env files (.env or root)
  4. Active ADC Credentials (google.auth.default)

Subcommands:
  uv run python governance.py registry-agents
  uv run python governance.py registry-search "intervals"
  uv run python governance.py list
  uv run python governance.py create "The coach must never schedule or alter workouts without explicit approval" --name no-unapproved-workout-modifications
  uv run python governance.py delete POLICY_ID
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time

from dotenv import load_dotenv
import google.auth
import google.auth.transport.requests
import httpx

# 1. Load local .env if present
_env_path = pathlib.Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
elif (pathlib.Path(__file__).parents[1] / ".env").exists():
    load_dotenv(pathlib.Path(__file__).parents[1] / ".env")


def resolve_project(cli_project: str | None = None) -> str:
    """Resolves project ID following the 4-tier fallback hierarchy."""
    if cli_project and cli_project.strip():
        return cli_project.strip()

    env_p = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
    if env_p and env_p.strip():
        return env_p.strip()

    try:
        _, adc_p = google.auth.default()
        if adc_p and adc_p.strip():
            return adc_p.strip()
    except Exception:
        pass

    sys.exit(
        "Set GOOGLE_CLOUD_PROJECT to your GCP project ID (export GOOGLE_CLOUD_PROJECT=...)"
    )


def resolve_region(cli_region: str | None = None) -> str:
    if cli_region and cli_region.strip():
        return cli_region.strip()
    return os.environ.get("GOOGLE_CLOUD_REGION") or os.environ.get("REGION") or "us-central1"


ENGINE_NAME = os.environ.get("AGENT_ENGINE_DISPLAY_NAME", "rowing-coach-agent")


def get_headers(project: str) -> dict:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {creds.token}",
        "x-goog-user-project": project,
    }


def call_api(method: str, url: str, project: str, **kw) -> dict:
    headers = get_headers(project)
    r = httpx.request(method, url, headers=headers, timeout=60, **kw)
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        err = body.get("error", {})
        sys.exit(
            f"{r.status_code} {err.get('status')}: {err.get('message')}\n"
            f"{json.dumps(err.get('details', []), indent=1)[:600]}"
        )
    return body


def cmd_registry_agents(args):
    project = resolve_project(args.project)
    region = resolve_region(args.region)
    url = f"https://agentregistry.googleapis.com/v1alpha/projects/{project}/locations/{region}/agents"
    agents = call_api("GET", url, project).get("agents", [])
    if not agents:
        print(f"No agents found in registry for {project} / {region}.")
        return
    print(f"{'DISPLAY NAME':36} {'ID'}")
    print("-" * 60)
    for a in agents:
        print(f"{a.get('displayName', ''):36} {a['name'].split('/')[-1]}")


def cmd_registry_search(args):
    project = resolve_project(args.project)
    url = f"https://agentregistry.googleapis.com/v1alpha/projects/{project}/locations/global/mcpServers:search"
    body = call_api("POST", url, project, json={"searchString": args.query})
    servers = body.get("mcpServers", [])
    if not servers:
        print(f"No MCP servers found matching '{args.query}'.")
        return
    for m in servers:
        tools = [t["name"] for t in m.get("tools", [])]
        print(f"{m.get('displayName', ''):36} {len(tools):>3} tools  {m.get('description', '')[:60]}")


def cmd_list_policies(args):
    project = resolve_project(args.project)
    region = resolve_region(args.region)
    url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/semanticGovernancePolicies"
    policies = call_api("GET", url, project).get("semanticGovernancePolicies", [])
    if not policies:
        print("No semantic governance policies active.")
        return
    print(f"{'POLICY NAME':40} {'STATE':12} {'DESCRIPTION'}")
    print("-" * 80)
    for p in policies:
        desc = p.get("description", "") or p.get("policyDirective", {}).get("instructions", "")
        print(f"{p['name'].split('/')[-1]:40} {p.get('state', 'UNKNOWN'):12} {desc[:60]}")


def cmd_create_policy(args):
    project = resolve_project(args.project)
    region = resolve_region(args.region)
    policy_name = args.name or f"rowing-coach-policy-{int(time.time())}"
    url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/semanticGovernancePolicies"
    payload = {
        "description": args.instruction,
        "policyDirective": {
            "instructions": args.instruction,
        },
    }
    res = call_api("POST", url, project, json=payload, params={"semanticGovernancePolicyId": policy_name})
    print(f"Created semantic governance policy: {policy_name}")
    print(json.dumps(res, indent=2))


def cmd_delete_policy(args):
    project = resolve_project(args.project)
    region = resolve_region(args.region)
    url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/semanticGovernancePolicies/{args.policy_id}"
    res = call_api("DELETE", url, project)
    print(f"Deleted policy: {args.policy_id}")


def main():
    parser = argparse.ArgumentParser(description="Rowing Coach Governance CLI")
    parser.add_argument("--project", help="Explicit GCP project ID")
    parser.add_argument("--region", help="GCP region (default: us-central1)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_agents = subparsers.add_parser("registry-agents", help="List agents in Agent Registry")
    p_agents.set_defaults(func=cmd_registry_agents)

    p_search = subparsers.add_parser("registry-search", help="Search MCP server catalog")
    p_search.add_argument("query", help="Search query (e.g. intervals, bigquery)")
    p_search.set_defaults(func=cmd_registry_search)

    p_list = subparsers.add_parser("list", help="List active semantic governance policies")
    p_list.set_defaults(func=cmd_list_policies)

    p_create = subparsers.add_parser("create-policy", help="Create a semantic governance policy")
    p_create.add_argument("instruction", help="Policy directive in natural language")
    p_create.add_argument("--name", help="Policy ID name")
    p_create.set_defaults(func=cmd_create_policy)

    p_del = subparsers.add_parser("delete-policy", help="Delete a policy by ID")
    p_del.add_argument("policy_id", help="Policy ID to delete")
    p_del.set_defaults(func=cmd_delete_policy)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
