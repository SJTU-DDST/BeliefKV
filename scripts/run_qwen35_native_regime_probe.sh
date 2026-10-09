#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
PORT="${PORT:-18454}"
ROOT_COUNT="${ROOT_COUNT:-108}"
SAMPLING_SEED="${SAMPLING_SEED:-21}"
ARRIVAL_BATCH_SIZE="${ARRIVAL_BATCH_SIZE:-0}"
ARRIVAL_BATCH_INTERVAL_MS="${ARRIVAL_BATCH_INTERVAL_MS:-0}"
HOST_SPLIT="${HOST_SPLIT:-80:20}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"
HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"
SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"
# Qwen3.5 advertises VLM support; its image warmup OOMs after 94% static KV sizing.
SKIP_SERVER_WARMUP="${SKIP_SERVER_WARMUP:-1}"
CONFIRMED_JOIN_CANARY="${CONFIRMED_JOIN_CANARY:-0}"
FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_2to4}"
AB_MODE="${AB_MODE:-off}"
NATIVE_POLICY_BASELINE="${NATIVE_POLICY_BASELINE:-0}"
PREPARE_HOST="${PREPARE_HOST:-0}"
CHILD_FINAL_REPORT_SHADOW="${CHILD_FINAL_REPORT_SHADOW:-1}"
H2D_SEED_ARTIFACT="${H2D_SEED_ARTIFACT:-$ROOT/experiments/models/native_h2d_ack_seed_20261004.json}"
TRANSFER_SERVICE_SEED="${TRANSFER_SERVICE_SEED:-$ROOT/experiments/models/native_transfer_service_seed_v4.json}"
TOOL_TIMING_ARTIFACT="${TOOL_TIMING_ARTIFACT:-$ROOT/experiments/models/qwen35_native_event_horizons_20260928_calibrated.json}"
ENABLE_TOOL_TIMING="${ENABLE_TOOL_TIMING:-1}"
PREFETCH_LEAD_MS="${PREFETCH_LEAD_MS:-1000}"
SEMANTIC_WORK_STATISTIC="${SEMANTIC_WORK_STATISTIC:-upper}"
EOS_PROTOCOL_WINDOW_MS="${EOS_PROTOCOL_WINDOW_MS:-50}"
TERMINAL_CACHE_DIAGNOSTICS="${TERMINAL_CACHE_DIAGNOSTICS:-1}"
SEMANTIC_REPORT_ARTIFACT="${SEMANTIC_REPORT_ARTIFACT:-$ROOT/experiments/models/child_semantic_work_frozen_phase_20261001_v1/semantic_event_calibrated.json}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_native_regime_${HICACHE_WRITE_POLICY}_${FANOUT_PROFILE}_${HICACHE_SIZE_GB}g_${HOST_SPLIT/:/_}_${ROOT_COUNT}root_v1}"
MANIFEST="${WORKLOAD_MANIFEST:-$ROOT/configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json}"
BASE_URL="http://127.0.0.1:$PORT"
SOCKET="/tmp/bkv-regime-${PORT}.sock"
server_pid=""

stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill -TERM "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
trap stop_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ $# -ne 0 || ! "$ROOT_COUNT" =~ ^[1-9][0-9]*$ ]] \
  || (( ROOT_COUNT > 128 )) \
  || [[ ! "$ARRIVAL_BATCH_SIZE" =~ ^[0-9]+$ || ! "$ARRIVAL_BATCH_INTERVAL_MS" =~ ^[0-9]+$ ]] \
  || { (( ROOT_COUNT > 108 )) && [[ "$ARRIVAL_BATCH_SIZE" != "64" || "$ARRIVAL_BATCH_INTERVAL_MS" == "0" ]]; } \
  || { [[ "$ARRIVAL_BATCH_SIZE" == "0" ]] && [[ "$ARRIVAL_BATCH_INTERVAL_MS" != "0" ]]; } \
  || [[ ! "$SAMPLING_SEED" =~ ^[0-9]+$ ]] \
  || [[ ! "$HICACHE_SIZE_GB" =~ ^[1-9][0-9]*$ ]] \
  || (( HICACHE_SIZE_GB > 200 )) \
  || [[ "$HICACHE_WRITE_POLICY" != "write_through_selective" && "$HICACHE_WRITE_POLICY" != "write_back" ]] \
  || [[ "$SKIP_SERVER_WARMUP" != "0" && "$SKIP_SERVER_WARMUP" != "1" ]] \
  || [[ "$CONFIRMED_JOIN_CANARY" != "0" && "$CONFIRMED_JOIN_CANARY" != "1" ]] \
  || [[ "$FANOUT_PROFILE" != "native_dynamic_1to4" && "$FANOUT_PROFILE" != "native_in_graph_1to4" && "$FANOUT_PROFILE" != "native_in_graph_2to4" ]] \
  || [[ "$AB_MODE" != "off" && "$AB_MODE" != "reactive" && "$AB_MODE" != "predictive_h2d" ]] \
  || [[ "$NATIVE_POLICY_BASELINE" != "0" && "$NATIVE_POLICY_BASELINE" != "1" ]] \
  || { [[ "$NATIVE_POLICY_BASELINE" == "1" ]] && [[ "$AB_MODE" != "off" || "$CONFIRMED_JOIN_CANARY" != "0" ]]; } \
  || [[ "$PREPARE_HOST" != "0" && "$PREPARE_HOST" != "1" ]] \
  || [[ "$CHILD_FINAL_REPORT_SHADOW" != "0" && "$CHILD_FINAL_REPORT_SHADOW" != "1" ]] \
  || [[ "$ENABLE_TOOL_TIMING" != "0" && "$ENABLE_TOOL_TIMING" != "1" ]] \
  || [[ ! "$PREFETCH_LEAD_MS" =~ ^[0-9]+$ ]] || (( PREFETCH_LEAD_MS < 100 || PREFETCH_LEAD_MS > 1000 )) \
  || [[ "$SEMANTIC_WORK_STATISTIC" != upper && "$SEMANTIC_WORK_STATISTIC" != center ]] \
  || [[ ! "$EOS_PROTOCOL_WINDOW_MS" =~ ^[0-9]+$ ]] || (( EOS_PROTOCOL_WINDOW_MS < 50 || EOS_PROTOCOL_WINDOW_MS > 500 )) \
  || [[ "$AB_MODE" != "off" && "$CONFIRMED_JOIN_CANARY" != "0" ]] \
  || [[ "$AB_MODE" == "predictive_h2d" && ! -f "$SEMANTIC_REPORT_ARTIFACT" ]] \
  || [[ "$CONFIRMED_JOIN_CANARY" == "1" && "$SGLANG_PATCH_FLAVOR" != "writeback_prepare" ]] \
  || { [[ "$HOST_SPLIT" != auto ]] \
    && { [[ ! "$HOST_SPLIT" =~ ^([1-9][0-9]?):([1-9][0-9]?)$ ]] \
      || (( ${BASH_REMATCH[1]:-0} + ${BASH_REMATCH[2]:-0} != 100 )); }; } \
  || [[ ! "$PORT" =~ ^[1-9][0-9]*$ ]] \
  || [[ -e "$RUN_ROOT" || -e "$SOCKET" ]]; then
  printf 'Usage: PORT=18454 ROOT_COUNT=108 ARRIVAL_BATCH_SIZE=0 ARRIVAL_BATCH_INTERVAL_MS=0 SAMPLING_SEED=21 HICACHE_SIZE_GB=200 HOST_SPLIT=80:20|auto HICACHE_WRITE_POLICY=write_back|write_through_selective SKIP_SERVER_WARMUP=1 CONFIRMED_JOIN_CANARY=0|1 FANOUT_PROFILE=native_in_graph_2to4|native_in_graph_1to4|native_dynamic_1to4 SGLANG_PATCH_FLAVOR=staging RUN_ROOT=<new path> bash %s\n' "$0" >&2
  exit 2
fi
if [[ -e /tmp/beliefkv-experiments.paused ]] \
  || curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null \
  || [[ "$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')" -lt 20971520 ]]; then
  printf 'Experiments paused, port occupied, or less than 20 GiB free\n' >&2
  exit 2
fi
mkdir -p "$RUN_ROOT/server" "$RUN_ROOT/opportunities"

server_flags=(
  --mamba-full-memory-ratio 0.9
)
control_flags=(--control-socket "$SOCKET")
telemetry_env=(BELIEFKV_ADMISSION_TELEMETRY_DIR="$RUN_ROOT/server"
  BELIEFKV_ADMISSION_OPPORTUNITY_DIR="$RUN_ROOT/opportunities")
if [[ "$NATIVE_POLICY_BASELINE" == "1" ]]; then
  control_flags=(--native-policy-baseline)
  telemetry_env=(BELIEFKV_NATIVE_TELEMETRY_DIR="$RUN_ROOT/server")
else
  server_flags+=(--enable-beliefkv-admission --beliefkv-event-socket-path "$SOCKET")
fi
if [[ "$CONFIRMED_JOIN_CANARY" == "1" ]]; then
  server_flags+=(--beliefkv-confirmed-join-canary)
fi
if [[ "$SKIP_SERVER_WARMUP" == "1" ]]; then
  server_flags+=(--skip-server-warmup)
fi
host_split_env=(BELIEFKV_FULL_MAMBA_HOST_SPLIT="$HOST_SPLIT")
ab_env=()
unset_env=(-u BELIEFKV_NATIVE_TELEMETRY_DIR)
if [[ "$NATIVE_POLICY_BASELINE" == "1" ]]; then
  unset_env=(-u BELIEFKV_ADMISSION_TELEMETRY_DIR -u BELIEFKV_ADMISSION_OPPORTUNITY_DIR
    -u BELIEFKV_SEMANTIC_REPORT_ARTIFACT -u BELIEFKV_TOOL_TIMING_ARTIFACT
    -u BELIEFKV_COMPLETION_LEAD_ARTIFACT -u BELIEFKV_COMPLETION_LEAD_SHA256
    -u BELIEFKV_H2D_SEED -u BELIEFKV_H2D_SEED_SHA256
    -u BELIEFKV_TOOL_TIMING_SHA256 -u BELIEFKV_TRANSFER_SERVICE_SEED
    -u BELIEFKV_TRANSFER_SERVICE_SEED_SHA256)
  ab_env=(BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=0 BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=0
    BELIEFKV_ENABLE_PREPARE_HOST=0 BELIEFKV_ENABLE_TOOL_PREFETCH=0
    BELIEFKV_ENABLE_EXECUTION_HANDOFF=0 BELIEFKV_ENABLE_RESIDENT_FIRST=0)
fi
if [[ "$AB_MODE" != "off" ]]; then
  unset_env=(-u BELIEFKV_NATIVE_TELEMETRY_DIR -u BELIEFKV_SEMANTIC_REPORT_ARTIFACT \
    -u BELIEFKV_COMPLETION_LEAD_ARTIFACT -u BELIEFKV_COMPLETION_LEAD_SHA256 \
    -u BELIEFKV_H2D_SEED -u BELIEFKV_H2D_SEED_SHA256 \
    -u BELIEFKV_TOOL_TIMING_ARTIFACT -u BELIEFKV_TOOL_TIMING_SHA256 \
    -u BELIEFKV_ENABLE_TOOL_PREFETCH \
    -u BELIEFKV_TRANSFER_SERVICE_SEED -u BELIEFKV_TRANSFER_SERVICE_SEED_SHA256)
  export BELIEFKV_EMIT_SEMANTIC_TEXT=1
  ab_env=(BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=1 BELIEFKV_ENABLE_PREPARE_HOST="$PREPARE_HOST"
    BELIEFKV_ENABLE_RESIDENT_FIRST=1
    BELIEFKV_PREFETCH_LEAD_MS="$PREFETCH_LEAD_MS"
    BELIEFKV_SEMANTIC_WORK_STATISTIC="$SEMANTIC_WORK_STATISTIC"
    BELIEFKV_EOS_PROTOCOL_WINDOW_MS="$EOS_PROTOCOL_WINDOW_MS"
    BELIEFKV_TERMINAL_CACHE_DIAGNOSTICS="$TERMINAL_CACHE_DIAGNOSTICS")
  if [[ "$ENABLE_TOOL_TIMING" == "1" ]]; then
    if [[ ! -f "$TOOL_TIMING_ARTIFACT" ]]; then
      printf 'Tool timing enabled but artifact is missing\n' >&2
      exit 2
    fi
    ab_env+=(BELIEFKV_TOOL_TIMING_ARTIFACT="$TOOL_TIMING_ARTIFACT"
      BELIEFKV_TOOL_TIMING_SHA256="$(sha256sum "$TOOL_TIMING_ARTIFACT" | cut -d' ' -f1)")
  fi
  if [[ -f "$TRANSFER_SERVICE_SEED" ]]; then
    ab_env+=(BELIEFKV_TRANSFER_SERVICE_SEED="$TRANSFER_SERVICE_SEED"
      BELIEFKV_TRANSFER_SERVICE_SEED_SHA256="$(sha256sum "$TRANSFER_SERVICE_SEED" | cut -d' ' -f1)")
  fi
  if [[ "$AB_MODE" == "reactive" ]]; then
    ab_env+=(BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=0 BELIEFKV_ENABLE_TOOL_PREFETCH=0
      BELIEFKV_ENABLE_EXECUTION_HANDOFF=0)
  else
    ab_env+=(BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=1 \
      BELIEFKV_ENABLE_EXECUTION_HANDOFF=1 \
      BELIEFKV_ENABLE_TOOL_PREFETCH="$ENABLE_TOOL_TIMING" \
      BELIEFKV_SEMANTIC_REPORT_ARTIFACT="$SEMANTIC_REPORT_ARTIFACT")
    if [[ -f "$H2D_SEED_ARTIFACT" ]]; then
      ab_env+=(BELIEFKV_H2D_SEED="$H2D_SEED_ARTIFACT" \
        BELIEFKV_H2D_SEED_SHA256="$(sha256sum "$H2D_SEED_ARTIFACT" | cut -d' ' -f1)")
    fi
  fi
fi
setsid env -u BELIEFKV_FULL_MAMBA_HOST_SPLIT "${unset_env[@]}" \
  "${ab_env[@]}" \
  "${host_split_env[@]}" PORT="$PORT" HICACHE_SIZE_GB="$HICACHE_SIZE_GB" \
  HICACHE_WRITE_POLICY="$HICACHE_WRITE_POLICY" \
  ENABLE_SESSION_RADIX_CACHE=1 HOST_NUMA_NODE=1 \
  MEM_FRACTION_STATIC=0.94 MAX_RUNNING_REQUESTS=48 \
  "${telemetry_env[@]}" \
  SGLANG_PATCH_FLAVOR="$SGLANG_PATCH_FLAVOR" \
  SGLANG_SOURCE_CHECKOUT="$ROOT/third_party/sglang-v0.5.20" \
  bash "$ROOT/scripts/launch_qwen35_native_v0520.sh" \
  "${server_flags[@]}" \
  > "$RUN_ROOT/server.log" 2>&1 &
server_pid="$!"

ready=false
for _ in $(seq 1 480); do
  if curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null \
    && [[ -f "$RUN_ROOT/server/native_telemetry_ready.json" ]] \
    && [[ -f "$RUN_ROOT/server/native_capacity_census.json" ]]; then
    ready=true
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  printf 'Native server did not become ready: %s\n' "$RUN_ROOT/server.log" >&2
  exit 1
fi

set +e
client_flags=()
if [[ "$CHILD_FINAL_REPORT_SHADOW" == "1" ]]; then
  client_flags+=(--child-final-report-shadow)
fi
"$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
  "${client_flags[@]}" \
  --mode autonomous --base-url "$BASE_URL/v1" --model Qwen3.5-35B-A3B \
  --workload-manifest "$MANIFEST" --max-workflows "$ROOT_COUNT" \
  --concurrency "$ROOT_COUNT" --subagent-fanout-profile "$FANOUT_PROFILE" \
  --workflow-arrival-batch-size "$ARRIVAL_BATCH_SIZE" \
  --workflow-arrival-batch-interval-ms "$ARRIVAL_BATCH_INTERVAL_MS" \
  --native-radix-sessions "${control_flags[@]}" \
  --server-audit "$RUN_ROOT/server/runtime_audit.jsonl" \
  --server-events "$RUN_ROOT/server/runtime_events.sglang.jsonl" \
  --server-log "$RUN_ROOT/server.log" --pool-tokens 1798995 \
  --model-context-tokens 131072 --max-completion-tokens 8192 \
  --sampling-seed "$SAMPLING_SEED" --recursion-limit 2048 \
  --activation-wall-clock-seconds "${ACTIVATION_WALL_CLOCK_SECONDS:-14400}" \
  --stream-completion-shadow --child-stream-content-shadow \
  --native-reactive-guard-profile --disable-completion-gate \
  --gate native --output "$RUN_ROOT/client_$ROOT_COUNT" \
  > "$RUN_ROOT/client_$ROOT_COUNT.log" 2>&1
client_status="$?"
set -e
stop_server
printf 'Native regime probe: client exit=%s, artifacts=%s\n' \
  "$client_status" "$RUN_ROOT"
exit "$client_status"
