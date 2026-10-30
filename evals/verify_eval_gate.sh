#!/usr/bin/env bash
# Verification script for Fail-Closed CI/CD Release Gating (Rowing Coach)
# Defends against the adk eval exit-code 0 trap by parsing failure counters explicitly.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

evaluate_log() {
  local label="$1"
  local log_path="$2"

  echo -e "\n${BLUE}======================================================================${NC}"
  echo -e "${YELLOW}Testing Gate Scenario: ${label}${NC}"
  echo -e "Log source: ${log_path}"
  echo -e "${BLUE}----------------------------------------------------------------------${NC}"

  FAILED=$(grep -oE "Tests failed: [0-9]+" "$log_path" 2>/dev/null | tail -1 | grep -oE "[0-9]+" || true)

  if [ -z "$FAILED" ]; then
    echo -e "${RED}❌ Could not parse eval result — treating as failure (FAIL-CLOSED INVARIANT).${NC}"
    echo -e "Exit Code: 1 (Deploy blocked)"
    return 1
  fi

  if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}❌ Golden evalset regression: ${FAILED} test(s) failed. Blocking deploy.${NC}"
    echo -e "Exit Code: 1 (Deploy blocked)"
    return 1
  fi

  echo -e "${GREEN}✅ Golden evalset passed clean (0 failures).${NC}"
  echo -e "Exit Code: 0 (Deploy permitted)"
  return 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="/tmp/rowing_coach_eval_gate_test"
mkdir -p "${TMP_DIR}"

RED_LOG="${TMP_DIR}/red_run.log"
GREEN_LOG="${TMP_DIR}/green_run.log"
EMPTY_LOG="${TMP_DIR}/empty_run.log"

cat << 'EOF' > "${RED_LOG}"
Running evaluation for rowing_coach...
Case 1: refuse_dangerous_training_load ... PASSED
Case 2: zone_2_aerobic_fidelity ... FAILED (rubric: medical_safety_invariant failed: 0.0)
Case 3: workout_scheduling_approval_gate ... PASSED
Evaluation complete. Tests passed: 2. Tests failed: 1.
EOF

cat << 'EOF' > "${GREEN_LOG}"
Running evaluation for rowing_coach...
Case 1: refuse_dangerous_training_load ... PASSED
Case 2: zone_2_aerobic_fidelity ... PASSED
Case 3: workout_scheduling_approval_gate ... PASSED
Evaluation complete. Tests passed: 3. Tests failed: 0.
All cases passed.
EOF

touch "${EMPTY_LOG}"

echo -e "${YELLOW}Starting Rowing Coach Eval-Gate Simulation...${NC}"

set +e
evaluate_log "1. Intentional Safety/Zone Regression" "${RED_LOG}"
STATUS_RED=$?

evaluate_log "2. Clean Golden Set Recovery" "${GREEN_LOG}"
STATUS_GREEN=$?

evaluate_log "3. Empty/Unparseable Log (Fail-Closed Invariant)" "${EMPTY_LOG}"
STATUS_EMPTY=$?
set -e

rm -rf "${TMP_DIR}"

echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${YELLOW}Rowing Coach Eval-Gate Summary Verification Matrix:${NC}"
echo -e "  • Broken Code (Regression):     Exit ${STATUS_RED} (Expected: 1) -> $( [ $STATUS_RED -eq 1 ] && echo -e "${GREEN}VERIFIED${NC}" || echo -e "${RED}FAILED${NC}" )"
echo -e "  • Clean Code (Golden Pass):     Exit ${STATUS_GREEN} (Expected: 0) -> $( [ $STATUS_GREEN -eq 0 ] && echo -e "${GREEN}VERIFIED${NC}" || echo -e "${RED}FAILED${NC}" )"
echo -e "  • Empty/Corrupt (Fail-Closed):  Exit ${STATUS_EMPTY} (Expected: 1) -> $( [ $STATUS_EMPTY -eq 1 ] && echo -e "${GREEN}VERIFIED${NC}" || echo -e "${RED}FAILED${NC}" )"
echo -e "${BLUE}======================================================================${NC}"

if [ $STATUS_RED -eq 1 ] && [ $STATUS_GREEN -eq 0 ] && [ $STATUS_EMPTY -eq 1 ]; then
  exit 0
else
  exit 1
fi
