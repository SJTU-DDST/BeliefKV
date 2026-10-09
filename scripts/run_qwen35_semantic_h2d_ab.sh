#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
ROOT_COUNT="${ROOT_COUNT:-108}"
HOST_SPLIT="${HOST_SPLIT:-80:20}"
PORT="${PORT:-18454}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_joint_wait_h2d_ab_${ROOT_COUNT}root_2to4_v8c}"
ARM_ORDER="${ARM_ORDER:-reactive predictive_h2d}"
SAMPLING_SEED="${SAMPLING_SEED:-21}"
REPETITION_ID="${REPETITION_ID:-0}"
ACTIVATION_WALL_CLOCK_SECONDS="${ACTIVATION_WALL_CLOCK_SECONDS:-14400}"
ARTIFACT="${SEMANTIC_REPORT_ARTIFACT:-$ROOT/experiments/models/child_semantic_work_live_v7_quantiles/semantic_event_calibrated.json}"
PREPARE_HOST="${PREPARE_HOST:-1}"
H2D_SEED_ARTIFACT="${H2D_SEED_ARTIFACT:-$ROOT/experiments/models/native_h2d_ack_seed_20261004.json}"
TOOL_TIMING_ARTIFACT="${TOOL_TIMING_ARTIFACT:-$ROOT/experiments/models/qwen35_native_event_horizons_20260928_calibrated.json}"
TRANSFER_SERVICE_SEED="${TRANSFER_SERVICE_SEED:-$ROOT/experiments/models/native_transfer_service_seed_v4.json}"
ENABLE_TOOL_TIMING="${ENABLE_TOOL_TIMING:-1}"
PREFETCH_LEAD_MS="${PREFETCH_LEAD_MS:-500}"
SEMANTIC_WORK_STATISTIC="${SEMANTIC_WORK_STATISTIC:-center}"
EOS_PROTOCOL_WINDOW_MS="${EOS_PROTOCOL_WINDOW_MS:-250}"
FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_2to4}"

if [[ $# -ne 0 || -e "$RUN_ROOT" || ! -f "$ARTIFACT" ]] \
  || [[ ! "$ROOT_COUNT" =~ ^[1-9][0-9]*$ ]] || (( ROOT_COUNT > 108 )) \
  || [[ ! "$SAMPLING_SEED" =~ ^[0-9]+$ || ! "$REPETITION_ID" =~ ^[0-9]+$ ]] \
  || [[ "$ARM_ORDER" != "predictive_h2d reactive" && "$ARM_ORDER" != "reactive predictive_h2d" && "$ARM_ORDER" != "predictive_h2d" ]]; then
  printf 'Usage: RUN_ROOT=<new path> ROOT_COUNT=108 FANOUT_PROFILE=native_in_graph_2to4 ARM_ORDER="reactive predictive_h2d"|"predictive_h2d reactive"|predictive_h2d SAMPLING_SEED=21 REPETITION_ID=0 ACTIVATION_WALL_CLOCK_SECONDS=14400 PORT=18454 bash %s\n' "$0" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"
export FANOUT_PROFILE
"$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
  --run-root "$RUN_ROOT" --initialize --root-count "$ROOT_COUNT" \
  --arm-order "$ARM_ORDER" --semantic-artifact "$ARTIFACT" \
  --activation-wall-clock-seconds "$ACTIVATION_WALL_CLOCK_SECONDS" \
  --prepare-host "$PREPARE_HOST" --h2d-seed "$H2D_SEED_ARTIFACT" \
  --host-split "$HOST_SPLIT" \
  --tool-timing-artifact "$TOOL_TIMING_ARTIFACT" \
  --enable-tool-timing "$ENABLE_TOOL_TIMING" --prefetch-lead-ms "$PREFETCH_LEAD_MS" \
  --transfer-service-seed "$TRANSFER_SERVICE_SEED" \
  --semantic-work-statistic "$SEMANTIC_WORK_STATISTIC" \
  --eos-protocol-window-ms "$EOS_PROTOCOL_WINDOW_MS" \
  --sampling-seed "$SAMPLING_SEED" --repetition-id "$REPETITION_ID" \
  --workload-manifest "${WORKLOAD_MANIFEST:-$ROOT/configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json}"
for arm in $ARM_ORDER; do
  printf 'Full %s arm: %s roots, fresh server and KV cache\n' "$arm" "$ROOT_COUNT"
  set +e
  AB_MODE="$arm" ROOT_COUNT="$ROOT_COUNT" PORT="$PORT" SAMPLING_SEED="$SAMPLING_SEED" \
    ARRIVAL_BATCH_SIZE=0 ARRIVAL_BATCH_INTERVAL_MS=0 \
    ACTIVATION_WALL_CLOCK_SECONDS="$ACTIVATION_WALL_CLOCK_SECONDS" \
    PREPARE_HOST="$PREPARE_HOST" H2D_SEED_ARTIFACT="$H2D_SEED_ARTIFACT" \
    ENABLE_TOOL_TIMING="$ENABLE_TOOL_TIMING" TOOL_TIMING_ARTIFACT="$TOOL_TIMING_ARTIFACT" \
    TRANSFER_SERVICE_SEED="$TRANSFER_SERVICE_SEED" PREFETCH_LEAD_MS="$PREFETCH_LEAD_MS" \
    SEMANTIC_WORK_STATISTIC="$SEMANTIC_WORK_STATISTIC" \
    EOS_PROTOCOL_WINDOW_MS="$EOS_PROTOCOL_WINDOW_MS" \
    FANOUT_PROFILE="$FANOUT_PROFILE" \
    HOST_SPLIT="$HOST_SPLIT" HICACHE_SIZE_GB=200 HICACHE_WRITE_POLICY=write_back \
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
  if [[ "$arm" == "predictive_h2d" ]]; then
    "$PYTHON" "$ROOT/scripts/audit_join_transfer_windows.py" \
      --arm "$RUN_ROOT/$arm" --output "$RUN_ROOT/$arm/join_transfer_windows.json"
    "$PYTHON" "$ROOT/scripts/audit_join_candidate_windows.py" \
      --arm "$RUN_ROOT/$arm" --artifact "$ARTIFACT" \
      --output "$RUN_ROOT/$arm/join_candidate_windows.json"
    "$PYTHON" "$ROOT/scripts/audit_tool_transfer_windows.py" \
      --arm "$RUN_ROOT/$arm" --output "$RUN_ROOT/$arm/tool_transfer_windows.json"
  fi
  "$PYTHON" "$ROOT/scripts/audit_native_h2d_sources.py" \
    --arm "$RUN_ROOT/$arm" --output "$RUN_ROOT/$arm/native_h2d_sources.json"
  "$PYTHON" "$ROOT/scripts/audit_prefetch_lifecycle.py" \
    --arm "$RUN_ROOT/$arm" --output "$RUN_ROOT/$arm/prefetch_lifecycle.json"
  "$PYTHON" "$ROOT/scripts/audit_native_memory_opportunity.py" \
    --arm "$RUN_ROOT/$arm" --output "$RUN_ROOT/$arm/memory_opportunity.json"
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --cleanup-arm "$RUN_ROOT/$arm" > "$RUN_ROOT/$arm.workspace_cleanup.json"
done
if [[ "$ARM_ORDER" == "predictive_h2d" ]]; then
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --run-root "$RUN_ROOT" --allow-incomplete
else
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" --run-root "$RUN_ROOT"
fi
