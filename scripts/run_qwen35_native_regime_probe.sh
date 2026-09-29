#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
PORT="${PORT:-18454}"
ROOT_COUNT="${ROOT_COUNT:-36}"
HOST_SPLIT="${HOST_SPLIT:-30:70}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"
HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"
SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"
# Qwen3.5 advertises VLM support; its image warmup OOMs after 94% static KV sizing.
SKIP_SERVER_WARMUP="${SKIP_SERVER_WARMUP:-1}"
CONFIRMED_JOIN_CANARY="${CONFIRMED_JOIN_CANARY:-0}"
FANOUT_PROFILE="${FANOUT_PROFILE:-native_in_graph_1to4}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_native_regime_${HICACHE_WRITE_POLICY}_${FANOUT_PROFILE}_${HICACHE_SIZE_GB}g_${HOST_SPLIT/:/_}_${ROOT_COUNT}root_v1}"
MANIFEST="$ROOT/configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json"
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
  || (( ROOT_COUNT > 64 )) \
  || [[ ! "$HICACHE_SIZE_GB" =~ ^[1-9][0-9]*$ ]] \
  || (( HICACHE_SIZE_GB > 200 )) \
  || [[ "$HICACHE_WRITE_POLICY" != "write_through_selective" && "$HICACHE_WRITE_POLICY" != "write_back" ]] \
  || [[ "$SKIP_SERVER_WARMUP" != "0" && "$SKIP_SERVER_WARMUP" != "1" ]] \
  || [[ "$CONFIRMED_JOIN_CANARY" != "0" && "$CONFIRMED_JOIN_CANARY" != "1" ]] \
  || [[ "$FANOUT_PROFILE" != "native_dynamic_1to4" && "$FANOUT_PROFILE" != "native_in_graph_1to4" ]] \
  || [[ "$CONFIRMED_JOIN_CANARY" == "1" && "$SGLANG_PATCH_FLAVOR" != "writeback_prepare" ]] \
  || [[ ! "$HOST_SPLIT" =~ ^([1-9][0-9]?):([1-9][0-9]?)$ ]] \
  || (( ${BASH_REMATCH[1]:-0} + ${BASH_REMATCH[2]:-0} != 100 )) \
  || [[ ! "$PORT" =~ ^[1-9][0-9]*$ ]] \
  || [[ -e "$RUN_ROOT" || -e "$SOCKET" ]]; then
  printf 'Usage: PORT=18454 ROOT_COUNT=36|32 HICACHE_SIZE_GB=200 HOST_SPLIT=30:70 HICACHE_WRITE_POLICY=write_back|write_through_selective SKIP_SERVER_WARMUP=1 CONFIRMED_JOIN_CANARY=0|1 FANOUT_PROFILE=native_in_graph_1to4|native_dynamic_1to4 SGLANG_PATCH_FLAVOR=staging RUN_ROOT=<new path> bash %s\n' "$0" >&2
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
  --enable-beliefkv-admission --beliefkv-event-socket-path "$SOCKET"
)
if [[ "$CONFIRMED_JOIN_CANARY" == "1" ]]; then
  server_flags+=(--beliefkv-confirmed-join-canary)
fi
if [[ "$SKIP_SERVER_WARMUP" == "1" ]]; then
  server_flags+=(--skip-server-warmup)
fi
setsid env PORT="$PORT" HICACHE_SIZE_GB="$HICACHE_SIZE_GB" \
  BELIEFKV_FULL_MAMBA_HOST_SPLIT="$HOST_SPLIT" \
  HICACHE_WRITE_POLICY="$HICACHE_WRITE_POLICY" \
  ENABLE_SESSION_RADIX_CACHE=1 HOST_NUMA_NODE=1 \
  MEM_FRACTION_STATIC=0.94 MAX_RUNNING_REQUESTS=48 \
  BELIEFKV_ADMISSION_TELEMETRY_DIR="$RUN_ROOT/server" \
  BELIEFKV_ADMISSION_OPPORTUNITY_DIR="$RUN_ROOT/opportunities" \
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
"$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
  --mode autonomous --base-url "$BASE_URL/v1" --model Qwen3.5-35B-A3B \
  --workload-manifest "$MANIFEST" --max-workflows "$ROOT_COUNT" \
  --concurrency "$ROOT_COUNT" --subagent-fanout-profile "$FANOUT_PROFILE" \
  --native-radix-sessions --control-socket "$SOCKET" \
  --server-audit "$RUN_ROOT/server/runtime_audit.jsonl" \
  --server-events "$RUN_ROOT/server/runtime_events.sglang.jsonl" \
  --server-log "$RUN_ROOT/server.log" --pool-tokens 1798995 \
  --model-context-tokens 131072 --max-completion-tokens 8192 \
  --sampling-seed 21 --recursion-limit 2048 \
  --activation-wall-clock-seconds 3600 \
  --native-reactive-guard-profile --disable-completion-gate \
  --gate native --output "$RUN_ROOT/client_$ROOT_COUNT" \
  > "$RUN_ROOT/client_$ROOT_COUNT.log" 2>&1
client_status="$?"
set -e
stop_server
printf 'Native regime probe: client exit=%s, artifacts=%s\n' \
  "$client_status" "$RUN_ROOT"
exit "$client_status"
