#!/usr/bin/env bash
# Bounded diagnostic: no predictive action and no formal training export.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_stream_completion_shadow_32root_20260925_v1}"
SOURCE="${WORKLOAD_SOURCE:-$ROOT/experiments/raw/qwen35_native_reactive_calibration_timing_v3_20260925/qwen35-native-reactive-calibration-66root-r0}"
BASE_URL="${BASE_URL:-http://127.0.0.1:18000}"
WORKLOAD_OFFSET="${WORKLOAD_OFFSET:-0}"
server_pid=""

stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill -TERM "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
trap stop_server EXIT

if [[ -e "$RUN_ROOT" ]]; then
  printf 'Refusing to overwrite prior pilot: %s\n' "$RUN_ROOT" >&2
  exit 1
fi
if curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null; then
  printf 'Pilot requires an unoccupied native server port\n' >&2
  exit 1
fi
if [[ ! -f "$SOURCE/runtime_workload_manifest.json" ]]; then
  printf 'Missing frozen calibration workload manifest\n' >&2
  exit 1
fi
if [[ ! "$WORKLOAD_OFFSET" =~ ^[0-9]+$ ]]; then
  printf 'WORKLOAD_OFFSET must be a nonnegative integer\n' >&2
  exit 1
fi
mapfile -t selected_instances < <(
  jq -r --argjson offset "$WORKLOAD_OFFSET" \
    '.workloads[$offset:($offset+32)][].instance_id' \
    "$SOURCE/runtime_workload_manifest.json"
)
if [[ "${#selected_instances[@]}" -ne 32 ]]; then
  printf 'Not enough distinct tasks starting at offset %s\n' "$WORKLOAD_OFFSET" >&2
  exit 1
fi
instance_args=()
if (( WORKLOAD_OFFSET > 0 )); then
  for instance in "${selected_instances[@]}"; do
    instance_args+=(--instance "$instance")
  done
fi
while IFS= read -r image; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    printf 'Required image is not cached: %s\n' "$image" >&2
    exit 1
  fi
done < <(
  jq -r --argjson offset "$WORKLOAD_OFFSET" \
    '.workloads[$offset:($offset+32)][].docker_image' \
    "$SOURCE/runtime_workload_manifest.json"
)

mkdir -p "$RUN_ROOT/server"
setsid env \
  HICACHE_SIZE_GB=120 \
  BELIEFKV_FULL_MAMBA_HOST_SPLIT=70:30 \
  HOST_NUMA_NODE=1 \
  MEM_FRACTION_STATIC=0.94 \
  MAX_RUNNING_REQUESTS=48 \
  BELIEFKV_NATIVE_TELEMETRY_DIR="$RUN_ROOT/server" \
  SGLANG_SOURCE_CHECKOUT="$ROOT/third_party/sglang-v0.5.20" \
  bash "$ROOT/scripts/launch_qwen35_native_v0520.sh" \
  > "$RUN_ROOT/server.log" 2>&1 &
server_pid="$!"

ready=false
for _ in $(seq 1 480); do
  if curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null \
    && [[ -f "$RUN_ROOT/server/native_telemetry_ready.json" ]]; then
    ready=true
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  printf 'Native server did not start; see %s\n' "$RUN_ROOT/server.log" >&2
  exit 1
fi

"$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
  --mode autonomous \
  --base-url "$BASE_URL/v1" \
  --model Qwen3.5-35B-A3B \
  --workload-manifest "$SOURCE/runtime_workload_manifest.json" \
  "${instance_args[@]}" \
  --max-workflows 32 \
  --concurrency 32 \
  --workflow-arrival-batch-size 16 \
  --workflow-arrival-batch-interval-ms 60000 \
  --subagent-fanout-profile native_dynamic_1to4 \
  --max-completion-tokens 8192 \
  --recursion-limit 2048 \
  --stream-completion-shadow \
  --activation-wall-clock-seconds 1800 \
  --disable-completion-gate \
  --gate system \
  --output "$RUN_ROOT/workloads" > "$RUN_ROOT/collection.log" 2>&1 \
  || collection_status="$?"
stop_server
if [[ ! -d "$RUN_ROOT/workloads/workflows" ]]; then
  printf 'Pilot produced no workflow events; see %s\n' "$RUN_ROOT/collection.log" >&2
  exit "${collection_status:-1}"
fi

for cue in first_content substantial_content; do
  if [[ "$cue" == substantial_content ]]; then
    for threshold in 64 1024 1700; do
      "$PYTHON" "$ROOT/scripts/audit_native_stream_shadow.py" \
        --workflows "$RUN_ROOT/workloads/workflows" \
        --cue "$cue" --content-threshold-chars "$threshold" \
        --output "$RUN_ROOT/${cue}_${threshold}_audit.json"
    done
  else
    "$PYTHON" "$ROOT/scripts/audit_native_stream_shadow.py" \
      --workflows "$RUN_ROOT/workloads/workflows" \
      --cue "$cue" --output "$RUN_ROOT/${cue}_audit.json"
  fi
done
exit "${collection_status:-0}"
