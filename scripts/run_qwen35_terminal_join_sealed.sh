#!/usr/bin/env bash
# Project-disjoint shadow validation; no predictive KV actions are enabled.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
FROZEN="$ROOT/configs/migration/qwen35_terminal_join_sealed_2026-09-27"
TRAIN_SOURCE="$ROOT/configs/migration/qwen35_native_reactive_overlapped_128root_workload_2026-09-23.json"
TRAIN_HIGH="$ROOT/experiments/raw/qwen35_cold_tool_overlapped_128root_train_20260927_v1/intent_workloads"
TRAIN_LOW="$ROOT/experiments/raw/qwen35_tool_join_low_pressure_train20_20260927_v1/intent_workloads"
OUT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_terminal_join_sealed_20260927_v1}"
PORT="${PORT:-18001}"
server_pid=""
background_pid=""
test_pid=""

stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill -TERM -- "-$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
  fi
}
cleanup() {
  if [[ -n "$background_pid" ]]; then
    kill "$background_pid" 2>/dev/null || true
    wait "$background_pid" 2>/dev/null || true
  fi
  if [[ -n "$test_pid" ]]; then
    kill "$test_pid" 2>/dev/null || true
    wait "$test_pid" 2>/dev/null || true
  fi
  stop_server
}
trap cleanup EXIT

if [[ -e "$OUT" || -f /tmp/beliefkv-experiments.paused ]]; then
  printf 'Output exists or GPU experiments are paused: %s\n' "$OUT" >&2
  exit 1
fi
if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  printf 'Port %s is already in use\n' "$PORT" >&2
  exit 1
fi
available_kib="$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')"
if (( available_kib < 80 * 1024 * 1024 )); then
  printf 'Need 80 GiB free for isolated 48-workflow validation\n' >&2
  exit 1
fi
expected_train_sha="$(jq -er '.training_source_sha256' "$FROZEN/provenance.json")"
printf '%s  %s\n' "$expected_train_sha" "$TRAIN_SOURCE" | sha256sum --check --status
while IFS=$'\t' read -r source digest; do
  printf '%s  %s\n' "$digest" "$ROOT/$source" | sha256sum --check --status
done < <(jq -r '.test_source_sha256 | to_entries[] | [.key, .value] | @tsv' "$FROZEN/provenance.json")
for arm in test background; do
  count="$(jq -er '.workloads | length' "$FROZEN/$arm.json")"
  if [[ "$count" != "$(jq -er --arg name "$arm" '.[$name + "_workflows"]' "$FROZEN/provenance.json")" ]]; then
    printf 'Frozen %s workflow count differs from provenance\n' "$arm" >&2
    exit 1
  fi
  while IFS= read -r image; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      printf 'Missing frozen sandbox image: %s\n' "$image" >&2
      exit 1
    fi
  done < <(jq -r '[.workloads[].docker_image] | unique[]' "$FROZEN/$arm.json")
done
for path in \
  "$TRAIN_HIGH/workflows" "$TRAIN_LOW/workflows" \
  "$TRAIN_HIGH/join_parent_first_gpu_service_train_20260927.json" \
  "$TRAIN_LOW/join_parent_first_gpu_service_train_20260927.json"; do
  if [[ ! -e "$path" ]]; then
    printf 'Missing frozen training-only evidence: %s\n' "$path" >&2
    exit 1
  fi
done

mkdir -p "$OUT/server"
git -C "$ROOT" rev-parse HEAD > "$OUT/source_commit.txt"
sha256sum "$FROZEN/"{test,background,provenance}.json > "$OUT/frozen_manifest_sha256.txt"
setsid env \
  PORT="$PORT" HICACHE_SIZE_GB=120 BELIEFKV_FULL_MAMBA_HOST_SPLIT=70:30 \
  HOST_NUMA_NODE=1 MEM_FRACTION_STATIC=0.94 MAX_RUNNING_REQUESTS=48 \
  BELIEFKV_NATIVE_TELEMETRY_DIR="$OUT/server" \
  SGLANG_SOURCE_CHECKOUT="$ROOT/third_party/sglang-v0.5.20" \
  bash "$ROOT/scripts/launch_qwen35_native_v0520.sh" \
  > "$OUT/server.log" 2>&1 &
server_pid="$!"

ready=false
for _ in $(seq 1 480); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 \
    && [[ -f "$OUT/server/native_telemetry_ready.json" ]]; then
    ready=true
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  printf 'Server failed to become ready: %s\n' "$OUT/server.log" >&2
  exit 1
fi

run_arm() {
  local arm="$1"
  local count="$2"
  "$PYTHON" "$ROOT/scripts/run_deepagents_swebench.py" \
    --mode autonomous --base-url "http://127.0.0.1:$PORT/v1" \
    --model Qwen3.5-35B-A3B --workload-manifest "$FROZEN/$arm.json" \
    --max-workflows "$count" --concurrency "$count" \
    --subagent-fanout-profile native_dynamic_1to4 \
    --max-completion-tokens 8192 --model-context-tokens 131072 \
    --recursion-limit 2048 --request-timeout 7200 \
    --activation-wall-clock-seconds 14400 \
    --native-reactive-guard-profile --disable-completion-gate --gate system \
    --stream-completion-shadow --child-finish-chunk-shadow \
    --child-return-intent-shadow \
    --output "$OUT/${arm}_workloads" > "$OUT/${arm}.log" 2>&1
}

run_arm background 32 &
background_pid="$!"
# Admit the sealed test after training-only load starts; its results never
# influence this arrival or the training-only timing priors.
for _ in $(seq 1 120); do
  if ! kill -0 "$background_pid" 2>/dev/null; then
    printf 'Background runner stopped before test admission\n' >&2
    wait "$background_pid" || true
    background_pid=""
    exit 1
  fi
  if [[ -s "$OUT/background_workloads/sglang_metrics.jsonl" ]] \
    && tail -n 1 "$OUT/background_workloads/sglang_metrics.jsonl" \
      | jq -e '.num_queue_reqs >= 9' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
run_arm test 16 &
test_pid="$!"

status=0
wait "$test_pid" || status=1
test_pid=""
wait "$background_pid" || status=1
background_pid=""
stop_server

if [[ -f "$OUT/test_workloads/summary.json" ]]; then
  "$PYTHON" "$ROOT/scripts/audit_join_parent_first_service.py" \
    --workloads "$OUT/test_workloads" \
    --output "$OUT/test_parent_first_service.json"
  "$PYTHON" "$ROOT/scripts/evaluate_qwen35_terminal_join_sealed.py" \
    --frozen-test "$FROZEN/test.json" \
    --provenance "$FROZEN/provenance.json" \
    --train "$TRAIN_HIGH/workflows" \
      "$TRAIN_HIGH/join_parent_first_gpu_service_train_20260927.json" \
    --train "$TRAIN_LOW/workflows" \
      "$TRAIN_LOW/join_parent_first_gpu_service_train_20260927.json" \
    --heldout "$OUT/test_workloads/workflows" \
      "$OUT/test_parent_first_service.json" \
    --output "$OUT/project_disjoint_service_gate.json"
fi
exit "$status"
