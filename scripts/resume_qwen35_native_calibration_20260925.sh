#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
TRAIN_ROOT="$ROOT/experiments/raw/qwen35_native_reactive_refit_20260924_v1"
BATCH="$TRAIN_ROOT/qwen35-native-reactive-overlapped-128root-train-r0"
DATASET="$BATCH/dataset_native_transfer_v2"
MODEL="$TRAIN_ROOT/frontier_qwen35_native_train_uncalibrated.json"
CAL_ROOT="$ROOT/experiments/raw/qwen35_native_reactive_calibration_20260925_v1"
CAL_DATASET="$CAL_ROOT/qwen35-native-reactive-calibration-66root-r0/dataset"
CAL_MODEL="$TRAIN_ROOT/frontier_qwen35_native_heads_calibrated.json"

printf 'Waiting for measured native transfer re-export\n'
while tmux has-session -t beliefkv_q35_reexport_20260925 2>/dev/null; do
  sleep 30
done
"$PYTHON" - "$DATASET/dataset_manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["formal_local_training_eligible"] is True
assert manifest["source"]["native_request_evidence"]["telemetry_complete"] is True
assert manifest["training_readiness"]["join_reentry_eligible_count"] > 0
assert manifest["training_readiness"]["pcie_service_eligible_count"] > 0
PY
printf 'Fitting train-only FrontierBelief checkpoint\n'
"$PYTHON" "$ROOT/scripts/train_qwen35_native_frontier.py" \
  --dataset-dir "$DATASET" \
  --model-version qwen35-native-reactive-refit-20260925-v1 \
  --output "$MODEL" > "$TRAIN_ROOT/train_native_transfer_v2.log" 2>&1

printf 'Starting held-out calibration collection\n'
env CALIBRATION_ROOT="$CAL_ROOT" \
  bash "$ROOT/scripts/run_qwen35_native_calibration_after_refit.sh"

printf 'Checking held-out native labels and calibrating supported heads\n'
"$PYTHON" "$ROOT/scripts/calibrate_frontier_belief.py" \
  --model "$MODEL" --dataset-dir "$CAL_DATASET" \
  --native-heads-only --output "$CAL_MODEL" \
  > "$CAL_ROOT/calibrate_native_heads.log" 2>&1
printf 'Native predictive heads calibrated: %s\n' "$CAL_MODEL"
