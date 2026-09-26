#!/usr/bin/env bash
# Development project holdout; opt-in notification never enables KV actions.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
MANIFEST="$ROOT/experiments/raw/qwen35_hidden_child_real_train_fresh32_20260926/workload_manifest.json"
PRIOR="$ROOT/experiments/raw/qwen35_child_completion_intent_pilot_20260926"
OUT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_child_intent_pylint_holdout_20260926}"
PORT="${PORT:-18001}"
SERVER_PID=""

stop_server() {
  if [[ -n "$SERVER_PID" ]]; then
    kill -TERM -- "-$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}
trap stop_server EXIT

if [[ -e "$OUT" || ! -f "$MANIFEST" ]]; then
  printf 'Existing output or missing frozen manifest: %s %s\n' "$OUT" "$MANIFEST" >&2
  exit 1
fi
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  printf 'Port %s is already in use\n' "$PORT" >&2
  exit 1
fi
if [[ -f /tmp/beliefkv-experiments.paused ]]; then
  printf 'Experiments are paused\n' >&2
  exit 1
fi
mapfile -t INSTANCES < <(
  jq -r '.workloads[] | select(.instance_id | startswith("pylint-dev__")) |
    .instance_id' "$MANIFEST"
)
if [[ "${#INSTANCES[@]}" -ne 4 ]]; then
  printf 'Expected exactly four frozen Pylint tasks\n' >&2
  exit 1
fi
while read -r image; do
  docker image inspect "$image" >/dev/null 2>&1 || {
    printf 'Missing frozen sandbox image: %s\n' "$image" >&2
    exit 1
  }
done < <(
  jq -r '.workloads[] | select(.instance_id | startswith("pylint-dev__")) |
    .docker_image' "$MANIFEST"
)

mkdir -p "$OUT/server"
sha256sum "$MANIFEST" > "$OUT/source_manifest.sha256"
git -C "$ROOT" rev-parse HEAD > "$OUT/source_commit.txt"
setsid env \
  PORT="$PORT" HICACHE_SIZE_GB=120 BELIEFKV_FULL_MAMBA_HOST_SPLIT=70:30 \
  HOST_NUMA_NODE=1 MEM_FRACTION_STATIC=0.94 MAX_RUNNING_REQUESTS=48 \
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
for instance in "${INSTANCES[@]}"; do
  instance_args+=(--instance "$instance")
done
failed=false
for arm in control intent; do
  intent_arg=()
  if [[ "$arm" == intent ]]; then
    intent_arg=(--child-return-intent-shadow)
  fi
  if ! "$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
    --mode autonomous --base-url "http://127.0.0.1:$PORT/v1" \
    --model Qwen3.5-35B-A3B --workload-manifest "$MANIFEST" \
    "${instance_args[@]}" --max-workflows 4 --concurrency 4 \
    --subagent-fanout-profile native_dynamic_1to4 \
    --max-completion-tokens 8192 --model-context-tokens 131072 \
    --recursion-limit 2048 --activation-wall-clock-seconds 900 \
    --disable-completion-gate --gate system \
    "${intent_arg[@]}" --output "$OUT/${arm}_workloads" \
    > "$OUT/${arm}.log" 2>&1; then
    printf '%s arm returned a nonzero status; inspect its trace\n' "$arm" >&2
    failed=true
  fi
done
stop_server
if [[ -d "$OUT/intent_workloads/workflows" ]]; then
  "$PYTHON" "$ROOT/scripts/audit_child_return_intent_shadow.py" \
    --workflows "$OUT/intent_workloads/workflows" \
    --output "$OUT/intent_audit.json"
  "$PYTHON" "$ROOT/scripts/evaluate_child_return_intent_timing.py" \
    --pilot-root "$PRIOR" --heldout-root "$OUT/intent_workloads" \
    --output "$OUT/frozen_project_holdout.json"
fi
if [[ "$failed" == true ]]; then
  exit 1
fi
