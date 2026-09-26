#!/usr/bin/env bash
# Evaluate only the complete, frozen project-disjoint cold-tool peer batch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
TRAIN="$ROOT/experiments/raw/qwen35_cold_tool_overlapped_128root_train_20260927_v1/intent_workloads"
HELDOUT="$ROOT/experiments/raw/qwen35_cold_tool_peer_holdout_66root_v1/intent_workloads"
OUTPUT="$ROOT/experiments/raw/qwen35_cold_tool_peer_holdout_66root_v1/holdout_evaluation"

if [[ -e "$OUTPUT" ]]; then
  printf 'Refusing to overwrite evaluation output: %s\n' "$OUTPUT" >&2
  exit 1
fi

"$PYTHON" -c '
import sys
from collections import Counter
from pathlib import Path
from scripts.evaluate_cold_tool_project_loo import require_complete_batch

train, heldout = (Path(arg) for arg in sys.argv[1:])
train_ids, _ = require_complete_batch(train / "workflows")
heldout_ids, _ = require_complete_batch(heldout / "workflows")
if len(train_ids) != 128 or len(heldout_ids) != 66:
    raise ValueError("the frozen training/heldout batch sizes changed")
if set(train_ids) & set(heldout_ids):
    raise ValueError("task IDs overlap")
train_projects = {task.split("__", 1)[0] for task in train_ids}
heldout_projects = Counter(task.split("__", 1)[0] for task in heldout_ids)
if train_projects & heldout_projects.keys():
    raise ValueError("projects overlap")
if heldout_projects != {"astropy": 22, "sphinx-doc": 44}:
    raise ValueError("unexpected heldout project composition")
' "$TRAIN" "$HELDOUT"

mkdir -p "$OUTPUT"
git -C "$ROOT" rev-parse HEAD > "$OUTPUT/evaluator_commit.txt"
sha256sum "$TRAIN/manifest.json" "$HELDOUT/manifest.json" \
  > "$OUTPUT/source_manifests.sha256"
args=(--train-workflows "$TRAIN/workflows" --heldout-workflows "$HELDOUT/workflows")

"$PYTHON" "$ROOT/scripts/evaluate_cold_tool_survival_landmarks.py" \
  "${args[@]}" --online-project-history \
  --output "$OUTPUT/cold_tool_survival_success.json"
"$PYTHON" "$ROOT/scripts/evaluate_cold_tool_survival_landmarks.py" \
  "${args[@]}" --online-project-history --include-returned-failures \
  --output "$OUTPUT/cold_tool_survival_returned_failures.json"
"$PYTHON" "$ROOT/scripts/evaluate_join_group_notice.py" \
  "${args[@]}" --output "$OUTPUT/join_group_notice.json"
"$PYTHON" "$ROOT/scripts/evaluate_child_return_intent_timing.py" \
  --pilot-root "$TRAIN" --heldout-root "$HELDOUT" \
  --output "$OUTPUT/child_return_intent.json"
printf 'Complete evaluation: %s\n' "$OUTPUT"
