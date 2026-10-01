#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
ROOT_COUNT="${ROOT_COUNT:-36}"
PORT="${PORT:-18454}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_semantic_h2d_ab_36root_20260930_v4}"
ARM_ORDER="${ARM_ORDER:-predictive_h2d reactive}"
ARTIFACT="${SEMANTIC_REPORT_ARTIFACT:-$ROOT/experiments/models/child_semantic_work_frozen_phase_20261001_v1/semantic_event_calibrated.json}"

if [[ $# -ne 0 || -e "$RUN_ROOT" || ! -f "$ARTIFACT" ]] \
  || [[ ! "$ROOT_COUNT" =~ ^[1-9][0-9]*$ ]] || (( ROOT_COUNT > 64 )) \
  || [[ "$ARM_ORDER" != "predictive_h2d reactive" && "$ARM_ORDER" != "reactive predictive_h2d" ]]; then
  printf 'Usage: RUN_ROOT=<new path> ROOT_COUNT=36 PORT=18454 bash %s\n' "$0" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"
"$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
  --run-root "$RUN_ROOT" --initialize --root-count "$ROOT_COUNT" \
  --arm-order "$ARM_ORDER" --semantic-artifact "$ARTIFACT"
for arm in $ARM_ORDER; do
  printf 'Full %s arm: %s roots, fresh server and KV cache\n' "$arm" "$ROOT_COUNT"
  set +e
  AB_MODE="$arm" ROOT_COUNT="$ROOT_COUNT" PORT="$PORT" \
    HOST_SPLIT=auto HICACHE_SIZE_GB=200 HICACHE_WRITE_POLICY=write_back \
    SGLANG_PATCH_FLAVOR=staging CONFIRMED_JOIN_CANARY=0 \
    SEMANTIC_REPORT_ARTIFACT="$ARTIFACT" RUN_ROOT="$RUN_ROOT/$arm" \
    bash "$ROOT/scripts/run_qwen35_native_regime_probe.sh" \
    > "$RUN_ROOT/$arm.launch.log" 2>&1
  status="$?"
  set -e
  printf '%s exit=%s\n' "$arm" "$status" >> "$RUN_ROOT/arm_status.txt"
  if [[ ! -f "$RUN_ROOT/$arm/client_$ROOT_COUNT/summary.json" ]]; then
    printf 'Arm did not produce a terminal workload summary; stop for investigation\n' >&2
    exit 1
  fi
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --run-root "$RUN_ROOT" --allow-incomplete
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --cleanup-arm "$RUN_ROOT/$arm" > "$RUN_ROOT/$arm.workspace_cleanup.json"
done
"$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" --run-root "$RUN_ROOT"
