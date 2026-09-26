#!/usr/bin/env bash
# Bounded two-stage timing pilot, not a predictive physical-transfer run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
SOURCE="$ROOT/experiments/raw/qwen35_native_reactive_calibration_timing_v3_20260925/qwen35-native-reactive-calibration-66root-r0/runtime_workload_manifest.json"
OUT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_child_intent_chunk_sphinx_20260926}"
PORT="${PORT:-18001}"
IDS=(
  sphinx-doc__sphinx-7748
  sphinx-doc__sphinx-7757
  sphinx-doc__sphinx-7889
  sphinx-doc__sphinx-7910
)
SERVER_PID=""

stop_server() {
  if [[ -n "$SERVER_PID" ]]; then
    kill -TERM -- "-$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}
trap stop_server EXIT

if [[ -e "$OUT" || ! -f "$SOURCE" ]]; then
  printf 'Existing output or missing frozen manifest: %s %s\n' "$OUT" "$SOURCE" >&2
  exit 1
fi
if [[ -f /tmp/beliefkv-experiments.paused ]] \
  || curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  printf 'Experiments are paused or port %s is occupied\n' "$PORT" >&2
  exit 1
fi
for id in "${IDS[@]}"; do
  image="$(jq -r --arg id "$id" \
    '[.workloads[]|select(.instance_id == $id)|.docker_image]|if length == 1 then .[0] else empty end' \
    "$SOURCE")"
  if [[ -z "$image" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    printf 'Missing frozen task or sandbox image: %s\n' "$id" >&2
    exit 1
  fi
done

mkdir -p "$OUT/server"
sha256sum "$SOURCE" > "$OUT/source_manifest.sha256"
git -C "$ROOT" rev-parse HEAD > "$OUT/source_commit.txt"
setsid env PORT="$PORT" HICACHE_SIZE_GB=120 \
  BELIEFKV_FULL_MAMBA_HOST_SPLIT=70:30 HOST_NUMA_NODE=1 \
  MEM_FRACTION_STATIC=0.94 MAX_RUNNING_REQUESTS=48 \
  BELIEFKV_NATIVE_TELEMETRY_DIR="$OUT/server" \
  SGLANG_SOURCE_CHECKOUT="$ROOT/third_party/sglang-v0.5.20" \
  bash "$ROOT/scripts/launch_qwen35_native_v0520.sh" \
  > "$OUT/server.log" 2>&1 &
SERVER_PID="$!"

ready=false
for _ in $(seq 1 480); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 \
    && [[ -f "$OUT/server/native_telemetry_ready.json" ]]; then
    ready=true
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  printf 'Server not ready: %s\n' "$OUT/server.log" >&2
  exit 1
fi

instance_args=()
for id in "${IDS[@]}"; do
  instance_args+=(--instance "$id")
done
failed=false
for arm in control intent; do
  intent_arg=()
  if [[ "$arm" == intent ]]; then
    intent_arg=(--child-return-intent-shadow)
  fi
  if ! "$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
    --mode autonomous --base-url "http://127.0.0.1:$PORT/v1" \
    --model Qwen3.5-35B-A3B --workload-manifest "$SOURCE" \
    "${instance_args[@]}" --max-workflows 4 --concurrency 4 \
    --subagent-fanout-profile native_dynamic_1to4 \
    --max-completion-tokens 8192 --model-context-tokens 131072 \
    --recursion-limit 2048 --activation-wall-clock-seconds 900 \
    --disable-completion-gate --gate system --stream-completion-shadow \
    "${intent_arg[@]}" --output "$OUT/${arm}_workloads" \
    > "$OUT/${arm}.log" 2>&1; then
    printf '%s arm incomplete; inspect individual workflows\n' "$arm" >&2
    failed=true
  fi
done
stop_server
if [[ -d "$OUT/intent_workloads/workflows" ]]; then
  "$PYTHON" "$ROOT/scripts/audit_child_return_intent_shadow.py" \
    --workflows "$OUT/intent_workloads/workflows" \
    --output "$OUT/intent_audit.json"
  "$PYTHON" "$ROOT/scripts/audit_child_intent_to_final_chunk.py" \
    --workflows "$OUT/intent_workloads/workflows" \
    --output "$OUT/intent_chunk_stages.json"
fi
if [[ "$failed" == true ]]; then
  exit 1
fi
