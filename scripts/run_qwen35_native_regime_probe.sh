#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
EXPERIMENT_PAUSE_FILE="${BELIEFKV_EXPERIMENT_PAUSE_FILE:-/tmp/beliefkv-experiments.paused}"
PORT="${PORT:-18454}"
ROOT_COUNT="${ROOT_COUNT:-108}"
MAX_RUNNING_REQUESTS="${MAX_RUNNING_REQUESTS:-48}"
SAMPLING_SEED="${SAMPLING_SEED:-21}"
SERVER_RANDOM_SEED="${SERVER_RANDOM_SEED:-21}"
MAX_COMPLETION_TOKENS="${MAX_COMPLETION_TOKENS:-16384}"
MODEL_REQUEST_TIMEOUT_SECONDS="${MODEL_REQUEST_TIMEOUT_SECONDS:-${ACTIVATION_WALL_CLOCK_SECONDS:-14400}}"
MODEL_CONTEXT_TOKENS="${MODEL_CONTEXT_TOKENS:-131072}"
CONTEXT_WINDOW_TOKENS="${CONTEXT_WINDOW_TOKENS:-32768}"
CONTEXT_KEEP_TOKENS="${CONTEXT_KEEP_TOKENS:-8192}"
CONTEXT_COMPRESSION_TRIGGER_RATIO="${CONTEXT_COMPRESSION_TRIGGER_RATIO:-}"
CONTEXT_TOKENIZER_PATH="${CONTEXT_TOKENIZER_PATH:-}"
AGENT_RECURSION_LIMIT="${AGENT_RECURSION_LIMIT:-2048}"
JOIN_PREFETCH_TRIGGER="${JOIN_PREFETCH_TRIGGER:-observed_eos}"
JOIN_PREDICTION_MIN_FULL_BYTES="${JOIN_PREDICTION_MIN_FULL_BYTES:-536870912}"
JOIN_PREDICTION_INTERVAL_MS="${JOIN_PREDICTION_INTERVAL_MS:-200}"
JOIN_PREDICTION_MAX_CANDIDATES="${JOIN_PREDICTION_MAX_CANDIDATES:-4}"
ONLINE_TOOL_TIMING="${ONLINE_TOOL_TIMING:-0}"
BORROWABLE_PARENT_HEADROOM="${BORROWABLE_PARENT_HEADROOM:-0}"
ARRIVAL_BATCH_SIZE="${ARRIVAL_BATCH_SIZE:-0}"
ARRIVAL_BATCH_INTERVAL_MS="${ARRIVAL_BATCH_INTERVAL_MS:-0}"
WORKFLOW_ARRIVAL_SCHEDULE="${WORKFLOW_ARRIVAL_SCHEDULE:-}"
HOST_SPLIT="${HOST_SPLIT:-80:20}"
HOST_NUMA_MEMORY_POLICY="${HOST_NUMA_MEMORY_POLICY:-preferred}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"
DEVICE_FULL_TOKENS="${DEVICE_FULL_TOKENS:-}"
DEVICE_MAMBA_SLOTS="${DEVICE_MAMBA_SLOTS:-}"
HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"
SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"
SGLANG_SOURCE_CHECKOUT="${SGLANG_SOURCE_CHECKOUT:-$ROOT/third_party/sglang-v0.5.20}"
# Qwen3.5 advertises VLM support; its image warmup OOMs after 94% static KV sizing.
SKIP_SERVER_WARMUP="${SKIP_SERVER_WARMUP:-1}"
CONFIRMED_JOIN_CANARY="${CONFIRMED_JOIN_CANARY:-0}"
FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_nested_2to4}"
AB_MODE="${AB_MODE:-off}"
NATIVE_POLICY_BASELINE="${NATIVE_POLICY_BASELINE:-0}"
CHAT_TOKEN_BLOCK_CACHE="${BELIEFKV_CHAT_TOKEN_BLOCK_CACHE:-0}"
if [[ "$NATIVE_POLICY_BASELINE" == "1" ]]; then
  CHAT_TOKEN_BLOCK_CACHE="${NATIVE_CHAT_TOKEN_BLOCK_CACHE:-0}"
elif [[ "$AB_MODE" == "predictive_h2d" ]]; then
  CHAT_TOKEN_BLOCK_CACHE="${PREDICTIVE_CHAT_TOKEN_BLOCK_CACHE:-$CHAT_TOKEN_BLOCK_CACHE}"
elif [[ "$AB_MODE" == "reactive" ]]; then
  CHAT_TOKEN_BLOCK_CACHE="${REACTIVE_CHAT_TOKEN_BLOCK_CACHE:-$CHAT_TOKEN_BLOCK_CACHE}"
fi
PREPARE_HOST="${PREPARE_HOST:-0}"
EXECUTION_HANDOFF="${EXECUTION_HANDOFF:-1}"
CHILD_FINAL_REPORT_SHADOW="${CHILD_FINAL_REPORT_SHADOW:-1}"
H2D_SEED_ARTIFACT="${H2D_SEED_ARTIFACT:-$ROOT/experiments/models/native_h2d_ack_seed_20261004.json}"
TRANSFER_SERVICE_SEED="${TRANSFER_SERVICE_SEED:-$ROOT/experiments/models/native_transfer_service_seed_v4.json}"
TOOL_TIMING_ARTIFACT="${TOOL_TIMING_ARTIFACT:-$ROOT/experiments/models/qwen35_native_event_horizons_20260928_calibrated.json}"
ENABLE_TOOL_TIMING="${ENABLE_TOOL_TIMING:-1}"
PREFETCH_LEAD_MS="${PREFETCH_LEAD_MS:-1000}"
PREFETCH_RESIDENCY_PROTECTION="${BELIEFKV_PREFETCH_RESIDENCY_PROTECTION:-${PREFETCH_RESIDENCY_PROTECTION:-1}}"
PREFILL_SCHEDULING_POLICY="${BELIEFKV_PREFILL_SCHEDULING_POLICY:-${PREFILL_SCHEDULING_POLICY:-workflow}}"
JOIN_LAST_CHILD_PRIORITY="${BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY:-${JOIN_LAST_CHILD_PRIORITY:-0}}"
JOIN_PREFETCH_MIN_ADVANCE_MS="${JOIN_PREFETCH_MIN_ADVANCE_MS:-0}"
TOOL_PREFETCH_MIN_ADVANCE_MS="${TOOL_PREFETCH_MIN_ADVANCE_MS:-0}"
PARALLEL_WAIT_PREFETCH="${PARALLEL_WAIT_PREFETCH:-0}"
WAIT_PREFETCH_CONTEXTS="${WAIT_PREFETCH_CONTEXTS:-1}"
PREDICTION_MAINTENANCE_INTERVAL_MS="${PREDICTION_MAINTENANCE_INTERVAL_MS:-0}"
H2D_OPPORTUNITY_SAMPLING="${H2D_OPPORTUNITY_SAMPLING:-0}"
SEMANTIC_WORK_STATISTIC="${SEMANTIC_WORK_STATISTIC:-upper}"
EOS_PROTOCOL_WINDOW_MS="${EOS_PROTOCOL_WINDOW_MS:-50}"
TERMINAL_CACHE_DIAGNOSTICS="${TERMINAL_CACHE_DIAGNOSTICS:-0}"
PROFILE_SHARED_PATH="${PROFILE_SHARED_PATH:-0}"
PREFETCH_PREFIX_DIAGNOSTICS="${PREFETCH_PREFIX_DIAGNOSTICS:-0}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_native_regime_${HICACHE_WRITE_POLICY}_${FANOUT_PROFILE}_${HICACHE_SIZE_GB}g_${HOST_SPLIT/:/_}_${ROOT_COUNT}root_v1}"
MANIFEST="${WORKLOAD_MANIFEST:-$ROOT/configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json}"
BASE_URL="http://127.0.0.1:$PORT"
SOCKET="/tmp/bkv-regime-${PORT}.sock"
server_pid=""
client_pid=""

stop_client() {
  if [[ -n "$client_pid" ]]; then
    kill -TERM -- "-$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
    client_pid=""
  fi
}

stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill -TERM "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
trap 'stop_client; stop_server' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ $# -ne 0 || ! "$ROOT_COUNT" =~ ^[1-9][0-9]*$ ]] \
  || (( ROOT_COUNT > 156 )) \
  || [[ ! "$ARRIVAL_BATCH_SIZE" =~ ^[0-9]+$ || ! "$ARRIVAL_BATCH_INTERVAL_MS" =~ ^[0-9]+$ ]] \
  || { [[ -z "$WORKFLOW_ARRIVAL_SCHEDULE" ]] && (( ROOT_COUNT > 108 )) && { [[ "$ARRIVAL_BATCH_INTERVAL_MS" == "0" ]] \
    || { [[ "$ARRIVAL_BATCH_SIZE" != "64" && "$ARRIVAL_BATCH_SIZE" != "108" ]]; }; }; } \
  || { [[ -z "$WORKFLOW_ARRIVAL_SCHEDULE" ]] && (( ROOT_COUNT > 128 )) && [[ "$ARRIVAL_BATCH_SIZE" != "108" ]]; } \
  || { [[ -n "$WORKFLOW_ARRIVAL_SCHEDULE" ]] && { [[ ! -f "$WORKFLOW_ARRIVAL_SCHEDULE" ]] \
    || [[ "$ARRIVAL_BATCH_SIZE" != "0" || "$ARRIVAL_BATCH_INTERVAL_MS" != "0" ]]; }; } \
  || { (( ARRIVAL_BATCH_SIZE > 108 )); } \
  || { (( ARRIVAL_BATCH_SIZE > 0 )) && (( ARRIVAL_BATCH_INTERVAL_MS == 0 )); } \
  || { [[ "$ARRIVAL_BATCH_SIZE" == "0" ]] && [[ "$ARRIVAL_BATCH_INTERVAL_MS" != "0" ]]; } \
  || [[ ! "$SAMPLING_SEED" =~ ^[0-9]+$ ]] \
  || [[ ! "$SERVER_RANDOM_SEED" =~ ^(0|[1-9][0-9]{0,9})$ ]] \
  || (( SERVER_RANDOM_SEED > 4294967295 )) \
  || [[ ! "$MAX_RUNNING_REQUESTS" =~ ^[1-9][0-9]{0,3}$ ]] \
  || [[ ! "$AGENT_RECURSION_LIMIT" =~ ^[1-9][0-9]{0,5}$ ]] \
  || [[ "$JOIN_PREFETCH_TRIGGER" != "observed_eos" && "$JOIN_PREFETCH_TRIGGER" != "report_service_or_eos" && "$JOIN_PREFETCH_TRIGGER" != "selective_report_or_eos" ]] \
  || [[ "$ONLINE_TOOL_TIMING" != "0" && "$ONLINE_TOOL_TIMING" != "1" ]] \
  || [[ "$BORROWABLE_PARENT_HEADROOM" != "0" && "$BORROWABLE_PARENT_HEADROOM" != "1" ]] \
  || [[ ! "$MAX_COMPLETION_TOKENS" =~ ^[1-9][0-9]*$ ]] \
  || [[ ! "$MODEL_CONTEXT_TOKENS" =~ ^[1-9][0-9]{0,6}$ ]] \
  || [[ ! "$CONTEXT_WINDOW_TOKENS" =~ ^[1-9][0-9]{0,6}$ ]] \
  || [[ ! "$CONTEXT_KEEP_TOKENS" =~ ^[1-9][0-9]{0,6}$ ]] \
  || (( CONTEXT_KEEP_TOKENS >= CONTEXT_WINDOW_TOKENS )) \
  || (( CONTEXT_WINDOW_TOKENS > MODEL_CONTEXT_TOKENS )) \
  || [[ ! "$HICACHE_SIZE_GB" =~ ^(0|[1-9][0-9]{0,2})$ ]] \
  || (( HICACHE_SIZE_GB > 200 )) \
  || { [[ "$HICACHE_SIZE_GB" == "0" ]] && [[ "$NATIVE_POLICY_BASELINE" != "1" || "$AB_MODE" != "off" ]]; } \
  || { [[ -n "$DEVICE_FULL_TOKENS" ]] && [[ ! "$DEVICE_FULL_TOKENS" =~ ^[1-9][0-9]{0,8}$ ]]; } \
  || { [[ -n "$DEVICE_MAMBA_SLOTS" ]] && [[ ! "$DEVICE_MAMBA_SLOTS" =~ ^[1-9][0-9]{0,8}$ ]]; } \
  || [[ "$HICACHE_WRITE_POLICY" != "write_through_selective" && "$HICACHE_WRITE_POLICY" != "write_back" ]] \
  || [[ "$SKIP_SERVER_WARMUP" != "0" && "$SKIP_SERVER_WARMUP" != "1" ]] \
  || [[ "$CONFIRMED_JOIN_CANARY" != "0" && "$CONFIRMED_JOIN_CANARY" != "1" ]] \
  || [[ "$FANOUT_PROFILE" != "native_dynamic_1to4" && "$FANOUT_PROFILE" != "native_in_graph_1to4" && "$FANOUT_PROFILE" != "native_in_graph_2to4" && "$FANOUT_PROFILE" != "native_in_graph_nested_2to4" && "$FANOUT_PROFILE" != "native_in_graph_dynamic_nested" ]] \
  || [[ "$AB_MODE" != "off" && "$AB_MODE" != "reactive" && "$AB_MODE" != "predictive_h2d" ]] \
  || [[ "$NATIVE_POLICY_BASELINE" != "0" && "$NATIVE_POLICY_BASELINE" != "1" ]] \
  || [[ "$CHAT_TOKEN_BLOCK_CACHE" != "0" && "$CHAT_TOKEN_BLOCK_CACHE" != "1" ]] \
  || { [[ "$NATIVE_POLICY_BASELINE" == "1" ]] && [[ "$AB_MODE" != "off" || "$CONFIRMED_JOIN_CANARY" != "0" ]]; } \
  || [[ "$PREPARE_HOST" != "0" && "$PREPARE_HOST" != "1" ]] \
  || [[ "$EXECUTION_HANDOFF" != "0" && "$EXECUTION_HANDOFF" != "1" ]] \
  || [[ "$CHILD_FINAL_REPORT_SHADOW" != "0" && "$CHILD_FINAL_REPORT_SHADOW" != "1" ]] \
  || [[ "$ENABLE_TOOL_TIMING" != "0" && "$ENABLE_TOOL_TIMING" != "1" ]] \
  || [[ "$PROFILE_SHARED_PATH" != "0" && "$PROFILE_SHARED_PATH" != "1" ]] \
  || [[ "$PREFETCH_PREFIX_DIAGNOSTICS" != "0" && "$PREFETCH_PREFIX_DIAGNOSTICS" != "1" ]] \
  || [[ "$PREFETCH_RESIDENCY_PROTECTION" != "0" && "$PREFETCH_RESIDENCY_PROTECTION" != "1" ]] \
  || [[ "$JOIN_LAST_CHILD_PRIORITY" != "0" && "$JOIN_LAST_CHILD_PRIORITY" != "1" ]] \
  || [[ "$PREFILL_SCHEDULING_POLICY" != "workflow" && "$PREFILL_SCHEDULING_POLICY" != "join_priority" && "$PREFILL_SCHEDULING_POLICY" != "workflow_completion" ]] \
  || [[ ! "$PREFETCH_LEAD_MS" =~ ^[0-9]+$ ]] \
  || [[ "$SEMANTIC_WORK_STATISTIC" != upper && "$SEMANTIC_WORK_STATISTIC" != center ]] \
  || [[ ! "$EOS_PROTOCOL_WINDOW_MS" =~ ^[0-9]+$ ]] || (( EOS_PROTOCOL_WINDOW_MS < 50 || EOS_PROTOCOL_WINDOW_MS > 500 )) \
  || [[ "$AB_MODE" != "off" && "$CONFIRMED_JOIN_CANARY" != "0" ]] \
  || [[ "$CONFIRMED_JOIN_CANARY" == "1" && "$SGLANG_PATCH_FLAVOR" != "writeback_prepare" ]] \
  || { [[ "$HOST_SPLIT" != auto ]] \
    && { [[ ! "$HOST_SPLIT" =~ ^([1-9][0-9]?):([1-9][0-9]?)$ ]] \
      || (( ${BASH_REMATCH[1]:-0} + ${BASH_REMATCH[2]:-0} != 100 )); }; } \
  || [[ ! "$PORT" =~ ^[1-9][0-9]*$ ]] \
  || [[ -e "$RUN_ROOT" || -e "$SOCKET" ]]; then
  printf 'Usage: PORT=18454 ROOT_COUNT=108 ARRIVAL_BATCH_SIZE=0 ARRIVAL_BATCH_INTERVAL_MS=0 SAMPLING_SEED=21 HICACHE_SIZE_GB=200 HOST_SPLIT=80:20|auto HICACHE_WRITE_POLICY=write_back|write_through_selective SKIP_SERVER_WARMUP=1 CONFIRMED_JOIN_CANARY=0|1 FANOUT_PROFILE=native_in_graph_nested_2to4|native_in_graph_2to4|native_in_graph_1to4|native_dynamic_1to4 SGLANG_PATCH_FLAVOR=staging RUN_ROOT=<new path> bash %s\n' "$0" >&2
  exit 2
fi
export BELIEFKV_CHAT_TOKEN_BLOCK_CACHE="$CHAT_TOKEN_BLOCK_CACHE"
export BELIEFKV_PREFETCH_PREFIX_DIAGNOSTICS="$PREFETCH_PREFIX_DIAGNOSTICS"
export BELIEFKV_PREFETCH_RESIDENCY_PROTECTION="$PREFETCH_RESIDENCY_PROTECTION"
export BELIEFKV_PREFILL_SCHEDULING_POLICY="$PREFILL_SCHEDULING_POLICY"
export BELIEFKV_ENABLE_JOIN_LAST_CHILD_PRIORITY="$JOIN_LAST_CHILD_PRIORITY"
"$PYTHON" -c 'import json,sys; rows=json.load(open(sys.argv[1]))["workloads"]; count=int(sys.argv[2]); assert count <= len(rows), "requested roots exceed manifest"; assert len({row["instance_id"] for row in rows[:count]}) == count, "duplicate workload"' "$MANIFEST" "$ROOT_COUNT"
if [[ -n "$WORKFLOW_ARRIVAL_SCHEDULE" ]]; then
  PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -c 'import json,sys; from pathlib import Path; from beliefkv.experiments.arrival_schedule import load_workflow_arrivals; rows=json.load(open(sys.argv[1]))["workloads"][:int(sys.argv[2])]; load_workflow_arrivals(Path(sys.argv[3]), instance_ids=[row["instance_id"] for row in rows])' "$MANIFEST" "$ROOT_COUNT" "$WORKFLOW_ARRIVAL_SCHEDULE"
fi
if [[ -e "$EXPERIMENT_PAUSE_FILE" ]] \
  || curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null \
  || [[ "$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')" -lt 20971520 ]]; then
  printf 'Experiments paused, port occupied, or less than 20 GiB free\n' >&2
  exit 2
fi
mkdir -p "$RUN_ROOT/server" "$RUN_ROOT/opportunities"

server_flags=(
  --mamba-full-memory-ratio 0.9
)
if [[ -n "$DEVICE_FULL_TOKENS" ]]; then
  server_flags+=(--max-total-tokens "$DEVICE_FULL_TOKENS")
fi
if [[ -n "$DEVICE_MAMBA_SLOTS" ]]; then
  server_flags+=(--max-mamba-cache-size "$DEVICE_MAMBA_SLOTS")
fi
control_flags=(--control-socket "$SOCKET")
telemetry_env=(BELIEFKV_ADMISSION_TELEMETRY_DIR="$RUN_ROOT/server"
  BELIEFKV_ADMISSION_OPPORTUNITY_DIR="$RUN_ROOT/opportunities"
  BELIEFKV_JOIN_PREFETCH_TRIGGER="$JOIN_PREFETCH_TRIGGER"
  BELIEFKV_JOIN_PREDICTION_MIN_FULL_BYTES="$JOIN_PREDICTION_MIN_FULL_BYTES"
  BELIEFKV_JOIN_PREDICTION_INTERVAL_MS="$JOIN_PREDICTION_INTERVAL_MS"
  BELIEFKV_JOIN_PREDICTION_MAX_CANDIDATES="$JOIN_PREDICTION_MAX_CANDIDATES"
  BELIEFKV_ONLINE_TOOL_TIMING="$ONLINE_TOOL_TIMING"
  BELIEFKV_BORROWABLE_PARENT_HEADROOM="$BORROWABLE_PARENT_HEADROOM")
if [[ "$NATIVE_POLICY_BASELINE" == "1" ]]; then
  export BELIEFKV_EMIT_SEMANTIC_TEXT=0
  control_flags=(--native-policy-baseline)
  telemetry_env=(BELIEFKV_NATIVE_TELEMETRY_DIR="$RUN_ROOT/server")
else
  server_flags+=(--enable-beliefkv-admission --beliefkv-event-socket-path "$SOCKET")
fi
telemetry_env+=(BELIEFKV_CHAT_TOKEN_BLOCK_CACHE="$CHAT_TOKEN_BLOCK_CACHE")
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
  ab_env=(BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=1 BELIEFKV_ENABLE_PREPARE_HOST="$PREPARE_HOST"
    BELIEFKV_ENABLE_RESIDENT_FIRST=1
    BELIEFKV_PREFETCH_LEAD_MS="$PREFETCH_LEAD_MS"
    BELIEFKV_JOIN_PREFETCH_MIN_ADVANCE_MS="$JOIN_PREFETCH_MIN_ADVANCE_MS"
    BELIEFKV_TOOL_PREFETCH_MIN_ADVANCE_MS="$TOOL_PREFETCH_MIN_ADVANCE_MS"
    BELIEFKV_PARALLEL_WAIT_PREFETCH="$PARALLEL_WAIT_PREFETCH"
    BELIEFKV_WAIT_PREFETCH_CONTEXTS="$WAIT_PREFETCH_CONTEXTS"
    BELIEFKV_PREDICTION_MAINTENANCE_INTERVAL_MS="$PREDICTION_MAINTENANCE_INTERVAL_MS"
    BELIEFKV_SEMANTIC_WORK_STATISTIC="$SEMANTIC_WORK_STATISTIC"
    BELIEFKV_EOS_PROTOCOL_WINDOW_MS="$EOS_PROTOCOL_WINDOW_MS"
    BELIEFKV_PROFILE_SHARED_PATH="$PROFILE_SHARED_PATH"
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
    export BELIEFKV_EMIT_SEMANTIC_TEXT=0
    ab_env+=(BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=0 BELIEFKV_ENABLE_TOOL_PREFETCH=0
      BELIEFKV_ENABLE_EXECUTION_HANDOFF=0 BELIEFKV_H2D_OPPORTUNITY_SAMPLING=0)
  else
    export BELIEFKV_EMIT_SEMANTIC_TEXT=0
    ab_env+=(BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=1 \
      BELIEFKV_H2D_OPPORTUNITY_SAMPLING="$H2D_OPPORTUNITY_SAMPLING" \
      BELIEFKV_ENABLE_EXECUTION_HANDOFF="$EXECUTION_HANDOFF" \
      BELIEFKV_ENABLE_TOOL_PREFETCH="$ENABLE_TOOL_TIMING")
    if [[ -f "$H2D_SEED_ARTIFACT" ]]; then
      ab_env+=(BELIEFKV_H2D_SEED="$H2D_SEED_ARTIFACT" \
        BELIEFKV_H2D_SEED_SHA256="$(sha256sum "$H2D_SEED_ARTIFACT" | cut -d' ' -f1)")
    fi
  fi
fi
if [[ "${BELIEFKV_INTERNAL_COMPARISON_MODE:-full}" == "kv_only" ]]; then
  ab_env+=(BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=0 BELIEFKV_ENABLE_RESIDENT_FIRST=0)
fi
if [[ "$PREFILL_SCHEDULING_POLICY" == "join_priority" ]]; then
  ab_env+=(BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=0 BELIEFKV_ENABLE_RESIDENT_FIRST=0)
fi
jq -n --argjson cache "$CHAT_TOKEN_BLOCK_CACHE" --argjson native "$NATIVE_POLICY_BASELINE" \
  --argjson prefixes "$PREFETCH_PREFIX_DIAGNOSTICS" \
  --argjson seed "$SERVER_RANDOM_SEED" \
  --arg comparison "${BELIEFKV_INTERNAL_COMPARISON_MODE:-full}" \
  --argjson protection "$PREFETCH_RESIDENCY_PROTECTION" \
  --arg scheduling "$PREFILL_SCHEDULING_POLICY" \
  --argjson last_child "$JOIN_LAST_CHILD_PRIORITY" \
  --arg tool_restore "${BELIEFKV_TOOL_RESTORE_MODE:-speculative}" \
  --arg idle_prepare "${BELIEFKV_IDLE_PREPARE_POLICY:-cold}" \
  --arg restore_tiers "${BELIEFKV_RESTORE_TIERS:-complete}" \
  '{schema_version: 1, chat_token_block_cache: ($cache == 1), native_policy_baseline: ($native == 1), prefetch_prefix_diagnostics: ($prefixes == 1), prefetch_residency_protection_requested: ($protection == 1), prefill_scheduling_policy_requested: $scheduling, join_last_child_priority_requested: ($last_child == 1), tool_restore_mode_requested: $tool_restore, idle_prepare_policy_requested: $idle_prepare, restore_tiers_requested: $restore_tiers, server_random_seed: $seed, internal_comparison_mode: $comparison}' \
  > "$RUN_ROOT/server/launch_configuration.json"
setsid env -u BELIEFKV_FULL_MAMBA_HOST_SPLIT "${unset_env[@]}" \
  "${ab_env[@]}" \
  "${host_split_env[@]}" PORT="$PORT" HICACHE_SIZE_GB="$HICACHE_SIZE_GB" \
  HICACHE_WRITE_POLICY="$HICACHE_WRITE_POLICY" \
  ENABLE_SESSION_RADIX_CACHE=1 HOST_NUMA_NODE=1 \
  HOST_NUMA_MEMORY_POLICY="$HOST_NUMA_MEMORY_POLICY" \
  MEM_FRACTION_STATIC=0.94 MAX_RUNNING_REQUESTS="$MAX_RUNNING_REQUESTS" \
  SERVER_RANDOM_SEED="$SERVER_RANDOM_SEED" \
  CONTEXT_LENGTH="$MODEL_CONTEXT_TOKENS" \
  "${telemetry_env[@]}" \
  SGLANG_PATCH_FLAVOR="$SGLANG_PATCH_FLAVOR" \
  SGLANG_SOURCE_CHECKOUT="$SGLANG_SOURCE_CHECKOUT" \
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
curl --silent --show-error --max-time 10 --fail \
  --output "$RUN_ROOT/server/server_info.json" "$BASE_URL/server_info"
jq --exit-status --argjson running "$MAX_RUNNING_REQUESTS" \
  --argjson context "$MODEL_CONTEXT_TOKENS" --argjson seed "$SERVER_RANDOM_SEED" \
  '.max_running_requests == $running and .context_length == $context and .random_seed == $seed' \
  "$RUN_ROOT/server/server_info.json" >/dev/null

# Skipped warmup can race the engine's POST with HTTP startup.
# Initialize both arms after health succeeds and before any workflow submits.
curl --silent --show-error --max-time 10 --fail --request POST \
  --output "$RUN_ROOT/server/gc_freeze_initialization.txt" "$BASE_URL/freeze_gc"

set +e
client_flags=()
if [[ -n "$CONTEXT_COMPRESSION_TRIGGER_RATIO" ]]; then
  client_flags+=(--context-compression-trigger-ratio "$CONTEXT_COMPRESSION_TRIGGER_RATIO")
fi
if [[ -n "$CONTEXT_TOKENIZER_PATH" ]]; then
  client_flags+=(--context-tokenizer-path "$CONTEXT_TOKENIZER_PATH")
fi
if [[ -n "$WORKFLOW_ARRIVAL_SCHEDULE" ]]; then
  client_flags+=(--workflow-arrival-schedule "$WORKFLOW_ARRIVAL_SCHEDULE")
fi
if [[ "$CHILD_FINAL_REPORT_SHADOW" == "1" ]]; then
  client_flags+=(--child-final-report-shadow)
fi
setsid "$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
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
  --model-context-tokens "$MODEL_CONTEXT_TOKENS" \
  --context-window-tokens "$CONTEXT_WINDOW_TOKENS" --context-keep-tokens "$CONTEXT_KEEP_TOKENS" \
  --max-completion-tokens "$MAX_COMPLETION_TOKENS" \
  --request-timeout "$MODEL_REQUEST_TIMEOUT_SECONDS" \
  --sampling-seed "$SAMPLING_SEED" --recursion-limit "$AGENT_RECURSION_LIMIT" \
  --sandbox-command-timeout 180 \
  --activation-wall-clock-seconds "${ACTIVATION_WALL_CLOCK_SECONDS:-14400}" \
  --stream-completion-shadow --child-stream-content-shadow \
  --child-finish-chunk-shadow --stream-http-timing-shadow \
  --native-reactive-guard-profile --disable-completion-gate \
  --gate native --output "$RUN_ROOT/client_$ROOT_COUNT" \
  > "$RUN_ROOT/client_$ROOT_COUNT.log" 2>&1 &
client_pid="$!"
while kill -0 "$client_pid" 2>/dev/null; do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    printf 'Native server exited during collection; stop client: %s\n' \
      "$RUN_ROOT/server.log" >&2
    kill -TERM -- "-$client_pid" 2>/dev/null || true
    break
  fi
  sleep 2
done
wait "$client_pid"
client_status="$?"
client_pid=""
set -e
stop_server
printf 'Native regime probe: client exit=%s, artifacts=%s\n' \
  "$client_status" "$RUN_ROOT"
exit "$client_status"
