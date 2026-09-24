#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRAIN_SESSION="${TRAIN_SESSION:-beliefkv_q35_refit_20260924}"
TRAIN_ROOT="${TRAIN_ROOT:-$ROOT/experiments/raw/qwen35_native_reactive_refit_20260924_v1}"
CALIBRATION_ROOT="${CALIBRATION_ROOT:-$ROOT/experiments/raw/qwen35_native_reactive_calibration_20260924_v1}"
PLAN="${PLAN:-$ROOT/configs/migration/qwen35_native_reactive_calibration_66root_plan_2026-09-24.json}"
MODEL="$TRAIN_ROOT/frontier_qwen35_native_train_uncalibrated.json"
TRAIN_DATASET="$TRAIN_ROOT/qwen35-native-reactive-overlapped-128root-train-r0/dataset/dataset_manifest.json"

printf 'Waiting for independent train collection and model refit: %s\n' "$TRAIN_SESSION"
while tmux has-session -t "$TRAIN_SESSION" 2>/dev/null; do
  sleep 30
done
if [[ ! -f "$MODEL" || ! -f "$TRAIN_DATASET" ]]; then
  printf 'Training did not finish and fit; calibration will not start\n' >&2
  exit 1
fi
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
"$PYTHON" - "$MODEL" "$TRAIN_DATASET" "$PLAN" <<'PY'
import json
import sys
from pathlib import Path

model, dataset, plan = (json.loads(Path(item).read_text()) for item in sys.argv[1:])
assert dataset["formal_local_training_eligible"] is True
assert dataset["source"]["native_request_evidence"]["telemetry_complete"] is True
assert dataset["source"]["collection_contract"]["split"] == "train"
assert plan["batches"][0]["split"] == "calibration"
assert set(model["metadata"]["fit_projects"]).isdisjoint(plan["batches"][0]["projects"])
PY
printf 'Train refit passed; starting project-disjoint calibration collection\n'
env PLAN="$PLAN" RUN_ROOT="$CALIBRATION_ROOT" \
  COLLECTION_SPLIT=calibration CAPACITY_CALIBRATION_MODE=capture \
  bash "$ROOT/scripts/run_qwen35_native_train_batches.sh"
