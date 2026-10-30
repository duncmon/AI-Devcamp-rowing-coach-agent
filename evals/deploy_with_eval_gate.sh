#!/usr/bin/env bash
# Production CI/CD Evaluation Gate for Rowing Coach Agent
# Executes live ADK evaluation gate before deploying to Vertex AI Reasoning Engine.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

EVAL_TARGET="${1:-evals/golden.json:refuse_dangerous_training_load}"
DEPLOY_ACTION="${2:-dry-run}"
LOG_FILE="/tmp/rowing_coach_eval_gate.log"

echo -e "${BLUE}======================================================================${NC}"
echo -e "${YELLOW}🧪 Stage 1: Executing Live ADK Evaluation Gate (${EVAL_TARGET})...${NC}"
echo -e "${BLUE}======================================================================${NC}"

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-ai-devcamp-duncan-395792}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

set +e
uv run adk eval rowing_coach "${EVAL_TARGET}" \
  --config_file_path evals/eval_config.json \
  2>&1 | tee "${LOG_FILE}"
EVAL_CLI_EXIT=$?
set -e

echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${YELLOW}🔍 Stage 2: Defensive Parsing & Gate Enforcement (Exit code $EVAL_CLI_EXIT)...${NC}"
echo -e "${BLUE}======================================================================${NC}"

FAILED=$(grep -oE "Tests failed: [0-9]+" "${LOG_FILE}" 2>/dev/null | tail -1 | grep -oE "[0-9]+" || true)

if [ -z "$FAILED" ]; then
  echo -e "${RED}❌ Could not parse eval result from log — treating as failure (FAIL-CLOSED INVARIANT).${NC}"
  echo -e "${RED}🛑 DEPLOYMENT BLOCKED.${NC}"
  exit 1
fi

if [ "$FAILED" -gt 0 ]; then
  echo -e "${RED}❌ Golden evalset regression detected: ${FAILED} test(s) failed.${NC}"
  echo -e "${RED}🛑 DEPLOYMENT BLOCKED. Pipeline aborted before deploy step.${NC}"
  exit 1
fi

echo -e "${GREEN}✅ All evaluation cases passed clean (0 failures).${NC}"
echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${GREEN}🚀 Stage 3: Triggering Production Deployment Gate...${NC}"
echo -e "${BLUE}======================================================================${NC}"

if [ "${DEPLOY_ACTION}" = "dry-run" ]; then
  echo -e "${GREEN}Deploy gate OPEN. [DRY-RUN]: Target deployment command:${NC}"
  echo -e "  -> agents-cli deploy --deployment-target agent_runtime"
  echo -e "${GREEN}Pipeline succeeded.${NC}"
  exit 0
else
  echo -e "${GREEN}Deploy gate OPEN. Executing live deployment action...${NC}"
  agents-cli deploy --deployment-target agent_runtime
fi
