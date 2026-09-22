#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
PLAN="${PLAN:-$ROOT/configs/migration/qwen35_native_reactive_train_plan_2026-09-22.json}"
SPLIT="${SPLIT:-$ROOT/configs/p6/swebench_verified_split_v1.json}"
RUN_ROOT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_native_reactive_train_20260922_v1}"
MODEL_PATH="${MODEL_PATH:-/srv/ai/models/Qwen/Qwen3.5-35B-A3B}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-180}"
MEM_FRACTION_STATIC="${MEM_FRACTION_STATIC:-0.94}"
HOST_NUMA_NODE="${HOST_NUMA_NODE:-1}"
BASE_URL="${BASE_URL:-http://127.0.0.1:18000}"
CAPACITY_CALIBRATION="${CAPACITY_CALIBRATION:-$ROOT/configs/migration/qwen35_native_hbm_capacity_2026-09-22.json}"
server_pid=""
batch_id="${BATCH_ID:-}"

stop_server() {
  if [[ -n "$server_pid" ]]; then
    kill -TERM "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    server_pid=""
    for _ in $(seq 1 60); do
      if ! curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null; then
        return
      fi
      sleep 1
    done
    printf 'Server still listening after shutdown\n' >&2
    exit 1
  fi
}
trap stop_server EXIT
trap 'printf "Interrupted during batch %s\n" "${batch:-startup}" >&2; exit 130' INT
trap 'printf "Terminated during batch %s\n" "${batch:-startup}" >&2; exit 143' TERM

if [[ $# -ne 0 ]]; then
  printf 'Usage: RUN_ROOT=... bash %s (no positional arguments)\n' "$0" >&2
  exit 2
fi
if [[ "$(jq -r '.plan_id' "$PLAN")" != "qwen35-native-reactive-v0520-v1" \
    && "$(jq -r '.plan_id' "$PLAN")" != "qwen35-native-reactive-v0520-v2" ]]; then
  printf 'Expected the frozen Qwen3.5 native train plan\n' >&2
  exit 2
fi
if [[ ! -f "$CAPACITY_CALIBRATION" ]]; then
  printf 'Train collection blocked: missing HBM pool calibration %s\n' "$CAPACITY_CALIBRATION" >&2
  exit 2
fi
if curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null; then
  printf 'Collection requires an unoccupied server port\n' >&2
  exit 2
fi

mkdir -p "$RUN_ROOT"
mapfile -t batches < <(jq -r '
  [.batches[] | select(.split == "train")]
  | sort_by(if .batch_id == "p6-017-train-mixed-r0" then 0 else 1 end)
  | .[].batch_id
' "$PLAN")
expected_batches=9
if [[ "$(jq -r '.plan_id' "$PLAN")" == "qwen35-native-reactive-v0520-v2" ]]; then
  expected_batches=1
fi
if [[ ${#batches[@]} -ne "$expected_batches" ]]; then
  printf 'Frozen train plan must have %s batches\n' "$expected_batches" >&2
  exit 2
fi
if [[ -n "$batch_id" ]]; then
  if ! printf '%s\n' "${batches[@]}" | grep -Fxq -- "$batch_id"; then
    printf 'Unknown train batch: %s\n' "$batch_id" >&2
    exit 2
  fi
  batches=("$batch_id")
fi

for batch in "${batches[@]}"; do
  printf 'Starting train batch %s\n' "$batch"
  run_dir="$RUN_ROOT/$batch"
  dataset_dir="$run_dir/dataset"
  if [[ -f "$dataset_dir/dataset_manifest.json" ]]; then
    "$PYTHON" - "$dataset_dir/dataset_manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["formal_local_training_eligible"] is True, "previous batch is ineligible"
assert manifest["source"]["native_request_evidence"]["telemetry_complete"] is True
PY
    printf 'Retaining already exported train batch %s\n' "$batch"
    continue
  fi
  if [[ -e "$run_dir/workloads" || -e "$run_dir/server" ]]; then
    printf 'Incomplete batch must be investigated before retry: %s\n' "$run_dir" >&2
    exit 1
  fi
  mkdir -p "$run_dir/server"

  requirements="$run_dir/image-requirements.json"
  locked=false
  if [[ -f "$requirements" ]]; then
    expected_images="$(jq -c --arg id "$batch" \
      '[.batches[] | select(.batch_id == $id) | .docker_images[]] | sort' "$PLAN")"
    actual_images="$(jq -c '[.images[].image] | sort' "$requirements")"
    if [[ "$(jq -r '.lock_state' "$requirements")" != frozen_local_images ]] \
        || [[ "$actual_images" != "$expected_images" ]]; then
      printf 'Existing image lock differs from frozen batch: %s\n' "$requirements" >&2
      exit 1
    fi
    locked=true
  else
    jq --arg id "$batch" '{
      image_count: ([.batches[] | select(.batch_id == $id) | .docker_images[]] | length),
      images: [.batches[] | select(.batch_id == $id) | .docker_images[] | {image: .}]
    }' "$PLAN" > "$requirements"
  fi
  while IFS= read -r image; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      docker pull "$image"
    fi
  done < <(jq -r '.images[].image' "$requirements")
  if jq -e --arg image \
      "swebench/sweb.eval.x86_64.psf_1776_requests-5414:latest" \
      'any(.images[].image; . == $image)' "$requirements" >/dev/null \
      && ! docker image inspect \
      beliefkv/sweb.eval.x86_64.psf_1776_requests-5414:harness-v1 >/dev/null 2>&1; then
    bash "$ROOT/scripts/build_p6_harness_images.sh"
  fi
  if [[ "$locked" != true ]]; then
    "$PYTHON" "$ROOT/scripts/lock_h200_pilot_images.py" \
      --requirements "$requirements"
  fi

  setsid env HICACHE_SIZE_GB="$HICACHE_SIZE_GB" HOST_NUMA_NODE="$HOST_NUMA_NODE" \
    MEM_FRACTION_STATIC="$MEM_FRACTION_STATIC" \
    BELIEFKV_NATIVE_TELEMETRY_DIR="$run_dir/server" \
    SGLANG_SOURCE_CHECKOUT="$ROOT/third_party/sglang-v0.5.20" \
    bash "$ROOT/scripts/launch_qwen35_native_v0520.sh" \
    > "$run_dir/server.log" 2>&1 &
  server_pid="$!"
  ready=false
  for _ in $(seq 1 480); do
    if curl --silent --max-time 2 --fail "$BASE_URL/health" >/dev/null \
        && [[ -f "$run_dir/server/native_telemetry_ready.json" ]] \
        && [[ -f "$run_dir/server/native_capacity_census.json" ]]; then
      ready=true
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if [[ "$ready" != true ]]; then
    printf 'Native server failed to become ready for %s\n' "$batch" >&2
    exit 1
  fi
  "$PYTHON" "$ROOT/scripts/calibrate_qwen35_hbm_pool.py" \
    --base-url "$BASE_URL" --telemetry-dir "$run_dir/server" \
    --model-path "$MODEL_PATH" \
    --mem-fraction-static "$MEM_FRACTION_STATIC" \
    --verify "$CAPACITY_CALIBRATION"

  "$PYTHON" "$ROOT/scripts/run_p6_collection_batch.py" \
    --collection-plan "$PLAN" --batch-id "$batch" \
    --native-telemetry-dir "$run_dir/server" \
    --image-lock "$run_dir/image-requirements.json" \
    --base-url "$BASE_URL/v1" --model Qwen3.5-35B-A3B \
    --expected-model-path "$MODEL_PATH" \
    --output "$run_dir/workloads" > "$run_dir/collection.log" 2>&1
  printf 'Collected train batch %s; stopping native server\n' "$batch"
  stop_server
  printf 'Native server stopped for %s; exporting\n' "$batch"
  "$PYTHON" "$ROOT/scripts/export_native_reactive_p6_dataset.py" "$run_dir" \
    --output-dir "$dataset_dir" --split-manifest "$SPLIT" \
    > "$run_dir/export.log" 2>&1
  if [[ "$(jq -r '.subagent_fanout_profile // "natural"' "$run_dir/workloads/summary.json")" \
      == native_subagent_2to3 ]]; then
    "$PYTHON" - "$run_dir" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
summary = json.loads((root / "workloads/summary.json").read_text())
manifest = json.loads((root / "dataset/dataset_manifest.json").read_text())
if summary["dynamic_subagent_count"] < 1 or not summary["join_type_counts"]:
    raise SystemExit("native child/JOIN workload emitted no SPAWN/JOIN")
if manifest["training_readiness"]["join_reentry_eligible_count"] < 1:
    raise SystemExit("native child/JOIN workload emitted no eligible JOIN label")
PY
  fi
  printf 'Exported train batch %s\n' "$batch"
done

if [[ -n "$batch_id" ]]; then
  printf 'Finished requested batch %s; model fitting requires all nine batches\n' "$batch_id"
  exit 0
fi

model="$RUN_ROOT/frontier_qwen35_native_train_uncalibrated.json"
if [[ -e "$model" ]]; then
  printf 'Model exists; refusing to replace: %s\n' "$model" >&2
  exit 1
fi
train_args=()
for batch in "${batches[@]}"; do
  train_args+=(--dataset-dir "$RUN_ROOT/$batch/dataset")
done
"$PYTHON" "$ROOT/scripts/train_qwen35_native_frontier.py" \
  "${train_args[@]}" --model-version qwen35-native-reactive-train-v1 \
  --output "$model" > "$RUN_ROOT/train.log" 2>&1
printf 'Fitted offline uncalibrated checkpoint: %s\n' "$model"
