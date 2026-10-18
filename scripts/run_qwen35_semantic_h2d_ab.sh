#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
ROOT_COUNT="${ROOT_COUNT:-108}"
ARRIVAL_BATCH_SIZE="${ARRIVAL_BATCH_SIZE:-0}"
ARRIVAL_BATCH_INTERVAL_MS="${ARRIVAL_BATCH_INTERVAL_MS:-0}"
ARRIVAL_RATE_PER_SECOND="${ARRIVAL_RATE_PER_SECOND:-0}"
ARRIVAL_SEED="${ARRIVAL_SEED:-21}"
HOST_SPLIT="${HOST_SPLIT:-80:20}"
HOST_NUMA_MEMORY_POLICY="${HOST_NUMA_MEMORY_POLICY:-preferred}"
PORT="${PORT:-18454}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_joint_wait_h2d_ab_${ROOT_COUNT}root_2to4_v8c}"
ARM_ORDER="${ARM_ORDER:-predictive_h2d}"
RESUME_PENDING="${RESUME_PENDING:-0}"
SAMPLING_SEED="${SAMPLING_SEED:-21}"
REPETITION_ID="${REPETITION_ID:-0}"
ACTIVATION_WALL_CLOCK_SECONDS="${ACTIVATION_WALL_CLOCK_SECONDS:-14400}"
ARTIFACT="${SEMANTIC_REPORT_ARTIFACT:-$ROOT/experiments/models/child_semantic_work_live_v7_quantiles/semantic_event_calibrated.json}"
PREPARE_HOST="${PREPARE_HOST:-1}"
EXECUTION_HANDOFF="${EXECUTION_HANDOFF:-1}"
H2D_SEED_ARTIFACT="${H2D_SEED_ARTIFACT:-$ROOT/experiments/models/native_h2d_ack_seed_20261004.json}"
TOOL_TIMING_ARTIFACT="${TOOL_TIMING_ARTIFACT:-$ROOT/experiments/models/qwen35_native_event_horizons_20260928_calibrated.json}"
TRANSFER_SERVICE_SEED="${TRANSFER_SERVICE_SEED:-$ROOT/experiments/models/native_transfer_service_seed_v4.json}"
ENABLE_TOOL_TIMING="${ENABLE_TOOL_TIMING:-1}"
PREFETCH_LEAD_MS="${PREFETCH_LEAD_MS:-500}"
JOIN_PREFETCH_MIN_ADVANCE_MS="${JOIN_PREFETCH_MIN_ADVANCE_MS:-0}"
TOOL_PREFETCH_MIN_ADVANCE_MS="${TOOL_PREFETCH_MIN_ADVANCE_MS:-0}"
PARALLEL_WAIT_PREFETCH="${PARALLEL_WAIT_PREFETCH:-0}"
WAIT_PREFETCH_CONTEXTS="${WAIT_PREFETCH_CONTEXTS:-1}"
PREDICTION_MAINTENANCE_INTERVAL_MS="${PREDICTION_MAINTENANCE_INTERVAL_MS:-0}"
H2D_OPPORTUNITY_SAMPLING="${H2D_OPPORTUNITY_SAMPLING:-0}"
TERMINAL_CACHE_DIAGNOSTICS="${TERMINAL_CACHE_DIAGNOSTICS:-0}"
PROFILE_SHARED_PATH="${PROFILE_SHARED_PATH:-0}"
SEMANTIC_WORK_STATISTIC="${SEMANTIC_WORK_STATISTIC:-center}"
EOS_PROTOCOL_WINDOW_MS="${EOS_PROTOCOL_WINDOW_MS:-250}"
FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_nested_2to4}"
NATIVE_REFERENCE_RUN="${NATIVE_REFERENCE_RUN:-}"
NATIVE_REFERENCE_TIMELINE="${NATIVE_REFERENCE_TIMELINE:-}"
NATIVE_REFERENCE_CACHE="${NATIVE_REFERENCE_CACHE:-}"
poisson_arrivals="$("$PYTHON" -c 'import math,sys; rate=float(sys.argv[1]); assert math.isfinite(rate) and rate >= 0, "arrival rate must be finite and non-negative"; print(int(rate > 0))' "$ARRIVAL_RATE_PER_SECOND")"
reference_args=()
reference_comparison_args=()
if [[ -n "$NATIVE_REFERENCE_RUN" ]]; then
  reference_args=(--native-reference-run "$NATIVE_REFERENCE_RUN")
  if [[ -n "$NATIVE_REFERENCE_TIMELINE" ]]; then
    reference_args+=(--native-reference-timeline "$NATIVE_REFERENCE_TIMELINE")
  fi
  if [[ -n "$NATIVE_REFERENCE_CACHE" ]]; then
    reference_args+=(--native-reference-cache "$NATIVE_REFERENCE_CACHE")
    reference_comparison_args+=(--native-reference-cache "$NATIVE_REFERENCE_CACHE")
  fi
elif [[ -n "$NATIVE_REFERENCE_TIMELINE" || -n "$NATIVE_REFERENCE_CACHE" ]]; then
  printf 'Native reference artifacts require NATIVE_REFERENCE_RUN\n' >&2
  exit 2
fi

if [[ $# -ne 0 || ! -f "$ARTIFACT" ]] \
  || [[ "$RESUME_PENDING" != "0" && "$RESUME_PENDING" != "1" ]] \
  || { [[ "$RESUME_PENDING" == "0" ]] && [[ -e "$RUN_ROOT" ]]; } \
  || { [[ "$RESUME_PENDING" == "1" ]] && [[ ! -f "$RUN_ROOT/ab_plan.json" ]]; } \
  || [[ ! "$ROOT_COUNT" =~ ^[1-9][0-9]*$ ]] || (( ROOT_COUNT > 156 )) \
  || [[ ! "$ARRIVAL_BATCH_SIZE" =~ ^[0-9]+$ || ! "$ARRIVAL_BATCH_INTERVAL_MS" =~ ^[0-9]+$ ]] \
  || { [[ "$poisson_arrivals" == "0" ]] && (( ROOT_COUNT > 108 )) && [[ "$ARRIVAL_BATCH_SIZE" != "108" || "$ARRIVAL_BATCH_INTERVAL_MS" == "0" ]]; } \
  || { [[ "$poisson_arrivals" == "1" ]] && [[ "$ARRIVAL_BATCH_SIZE" != "0" || "$ARRIVAL_BATCH_INTERVAL_MS" != "0" ]]; } \
  || [[ ! "$ARRIVAL_SEED" =~ ^[0-9]+$ ]] \
  || [[ ! "$SAMPLING_SEED" =~ ^[0-9]+$ || ! "$REPETITION_ID" =~ ^[0-9]+$ ]] \
  || [[ "$EXECUTION_HANDOFF" != "0" && "$EXECUTION_HANDOFF" != "1" ]] \
  || [[ "$ARM_ORDER" != "predictive_h2d reactive" && "$ARM_ORDER" != "reactive predictive_h2d" && "$ARM_ORDER" != "predictive_h2d" && "$ARM_ORDER" != "reactive" && "$ARM_ORDER" != "native predictive_h2d" && "$ARM_ORDER" != "predictive_h2d native" ]]; then
  printf 'Usage: RUN_ROOT=<new path> ROOT_COUNT=108|156 ARRIVAL_BATCH_SIZE=0|108 ARRIVAL_BATCH_INTERVAL_MS=0|3600000 FANOUT_PROFILE=native_in_graph_2to4 ARM_ORDER="predictive_h2d native"|"predictive_h2d reactive"|predictive_h2d|reactive SAMPLING_SEED=21 REPETITION_ID=0 ACTIVATION_WALL_CLOCK_SECONDS=14400 PORT=18454 bash %s\n' "$0" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"
export FANOUT_PROFILE HOST_NUMA_MEMORY_POLICY
if [[ "$RESUME_PENDING" == "0" ]]; then
"$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
  --run-root "$RUN_ROOT" --initialize --root-count "$ROOT_COUNT" \
  --arrival-batch-size "$ARRIVAL_BATCH_SIZE" --arrival-batch-interval-ms "$ARRIVAL_BATCH_INTERVAL_MS" \
  --arrival-rate-per-second "$ARRIVAL_RATE_PER_SECOND" --arrival-seed "$ARRIVAL_SEED" \
  --arm-order "$ARM_ORDER" --semantic-artifact "$ARTIFACT" \
  --activation-wall-clock-seconds "$ACTIVATION_WALL_CLOCK_SECONDS" \
  --prepare-host "$PREPARE_HOST" --execution-handoff "$EXECUTION_HANDOFF" \
  --h2d-seed "$H2D_SEED_ARTIFACT" \
  --host-split "$HOST_SPLIT" \
  --tool-timing-artifact "$TOOL_TIMING_ARTIFACT" \
  --enable-tool-timing "$ENABLE_TOOL_TIMING" --prefetch-lead-ms "$PREFETCH_LEAD_MS" \
  --join-prefetch-min-advance-ms "$JOIN_PREFETCH_MIN_ADVANCE_MS" \
  --tool-prefetch-min-advance-ms "$TOOL_PREFETCH_MIN_ADVANCE_MS" \
  --parallel-wait-prefetch "$PARALLEL_WAIT_PREFETCH" \
  --wait-prefetch-contexts "$WAIT_PREFETCH_CONTEXTS" \
  --prediction-maintenance-interval-ms "$PREDICTION_MAINTENANCE_INTERVAL_MS" \
  --h2d-opportunity-sampling "$H2D_OPPORTUNITY_SAMPLING" \
  --terminal-cache-diagnostics "$TERMINAL_CACHE_DIAGNOSTICS" \
  --profile-shared-path "$PROFILE_SHARED_PATH" \
  --transfer-service-seed "$TRANSFER_SERVICE_SEED" \
  --semantic-work-statistic "$SEMANTIC_WORK_STATISTIC" \
  --eos-protocol-window-ms "$EOS_PROTOCOL_WINDOW_MS" \
  --sampling-seed "$SAMPLING_SEED" --repetition-id "$REPETITION_ID" \
  "${reference_args[@]}" \
  --workload-manifest "${WORKLOAD_MANIFEST:-$ROOT/configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json}"
fi
arrival_schedule=""
if [[ "$poisson_arrivals" == "1" ]]; then
  arrival_schedule="$RUN_ROOT/ab_plan.json"
fi
for arm in $ARM_ORDER; do
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --run-root "$RUN_ROOT" --verify-frozen-plan
  if [[ "$RESUME_PENDING" == "1" && "$arm" == "native" \
    && -f "$RUN_ROOT/$arm/client_$ROOT_COUNT/summary.json" ]]; then
    printf 'Reuse collected native arm; continue with pending predictive\n'
    continue
  fi
  if [[ -e "$RUN_ROOT/$arm" ]]; then
    printf 'Arm directory already exists: %s\n' "$RUN_ROOT/$arm" >&2
    exit 1
  fi
  printf 'Full %s arm: %s roots, fresh server and KV cache\n' "$arm" "$ROOT_COUNT"
  baseline=0
  mode="$arm"
  if [[ "$arm" == "native" ]]; then
    baseline=1
    mode=off
  fi
  set +e
  NATIVE_POLICY_BASELINE="$baseline" AB_MODE="$mode" ROOT_COUNT="$ROOT_COUNT" PORT="$PORT" SAMPLING_SEED="$SAMPLING_SEED" \
    ARRIVAL_BATCH_SIZE="$ARRIVAL_BATCH_SIZE" ARRIVAL_BATCH_INTERVAL_MS="$ARRIVAL_BATCH_INTERVAL_MS" \
    WORKFLOW_ARRIVAL_SCHEDULE="$arrival_schedule" \
    ACTIVATION_WALL_CLOCK_SECONDS="$ACTIVATION_WALL_CLOCK_SECONDS" \
    PREPARE_HOST="$PREPARE_HOST" EXECUTION_HANDOFF="$EXECUTION_HANDOFF" \
    H2D_SEED_ARTIFACT="$H2D_SEED_ARTIFACT" \
    ENABLE_TOOL_TIMING="$ENABLE_TOOL_TIMING" TOOL_TIMING_ARTIFACT="$TOOL_TIMING_ARTIFACT" \
    TRANSFER_SERVICE_SEED="$TRANSFER_SERVICE_SEED" PREFETCH_LEAD_MS="$PREFETCH_LEAD_MS" \
    JOIN_PREFETCH_MIN_ADVANCE_MS="$JOIN_PREFETCH_MIN_ADVANCE_MS" \
    TOOL_PREFETCH_MIN_ADVANCE_MS="$TOOL_PREFETCH_MIN_ADVANCE_MS" \
    PARALLEL_WAIT_PREFETCH="$PARALLEL_WAIT_PREFETCH" WAIT_PREFETCH_CONTEXTS="$WAIT_PREFETCH_CONTEXTS" \
    PREDICTION_MAINTENANCE_INTERVAL_MS="$PREDICTION_MAINTENANCE_INTERVAL_MS" \
    H2D_OPPORTUNITY_SAMPLING="$H2D_OPPORTUNITY_SAMPLING" \
    TERMINAL_CACHE_DIAGNOSTICS="$TERMINAL_CACHE_DIAGNOSTICS" \
    PROFILE_SHARED_PATH="$PROFILE_SHARED_PATH" \
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
  "$PYTHON" "$ROOT/scripts/render_p6_execution_timeline.py" \
    --run-dir "$RUN_ROOT/$arm" --arm "$arm" --output-html "$RUN_ROOT/timelines/$arm.html" \
    > "$RUN_ROOT/$arm.timeline_export.json"
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --cleanup-arm "$RUN_ROOT/$arm" > "$RUN_ROOT/$arm.workspace_cleanup.json"
done
if [[ "$ARM_ORDER" == "predictive_h2d" || "$ARM_ORDER" == "reactive" ]]; then
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" \
    --run-root "$RUN_ROOT" --allow-incomplete
  if [[ -n "$NATIVE_REFERENCE_RUN" ]]; then
    "$PYTHON" "$ROOT/scripts/compare_native_policy_runs.py" \
      --arm "$ARM_ORDER=$RUN_ROOT/$ARM_ORDER" \
      --arm "native=$NATIVE_REFERENCE_RUN/native" \
      --timeline "$ARM_ORDER=$RUN_ROOT/timelines/$ARM_ORDER.json.gz" \
      --timeline "native=${NATIVE_REFERENCE_TIMELINE:-$NATIVE_REFERENCE_RUN/timelines/native.json.gz}" \
      --native-reference-run "$NATIVE_REFERENCE_RUN" \
      "${reference_comparison_args[@]}" \
      --output "$RUN_ROOT/native_reference_comparison.json"
  fi
else
  "$PYTHON" "$ROOT/scripts/summarize_semantic_h2d_ab.py" --run-root "$RUN_ROOT"
  comparison_args=()
  for arm in $ARM_ORDER; do
    comparison_args+=(--arm "$arm=$RUN_ROOT/$arm"
      --timeline "$arm=$RUN_ROOT/timelines/$arm.json.gz")
  done
  "$PYTHON" "$ROOT/scripts/compare_native_policy_runs.py" "${comparison_args[@]}" \
    --output "$RUN_ROOT/native_policy_comparison.json"
fi
