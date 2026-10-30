#!/usr/bin/env bash
# Runs the golden evaluation set for Rowing Coach Agent with fail-closed gate.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

EVAL_SET="${1:-evals/golden.json}"
CONFIG="${2:-evals/eval_config.json}"
LOG_FILE="/tmp/rowing_coach_eval_$(date +%s).log"

echo "======================================================================="
echo "Running ADK Evaluation for Rowing Coach Agent"
echo "Eval Set: ${EVAL_SET}"
echo "Config:   ${CONFIG}"
echo "Log:      ${LOG_FILE}"
echo "======================================================================="

# Run adk eval and tee to log
set +e
uv run adk eval rowing_coach \
  --eval_dataset_file_path_or_dir "${EVAL_SET}" \
  --config_file_path "${CONFIG}" \
  --print_detailed_eval_result \
  2>&1 | tee "${LOG_FILE}"
EVAL_EXIT=$?
set -e

echo "Parsing evaluation summary for fail-closed gate..."
FAILED=$(grep -oE "Tests failed: [0-9]+" "${LOG_FILE}" 2>/dev/null | tail -1 | grep -oE "[0-9]+" || true)

if [ -z "$FAILED" ]; then
  echo "❌ FAIL-CLOSED GATE: Unable to parse test failure counter. Blocking deployment."
  exit 1
fi

if [ "$FAILED" -gt 0 ]; then
  echo "❌ FAIL-CLOSED GATE: ${FAILED} test(s) failed in evalset. Blocking deployment."
  exit 1
fi

echo "✅ FAIL-CLOSED GATE: All tests passed cleanly (0 failures). Deployment permitted."
exit 0
