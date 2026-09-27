#!/usr/bin/env bash
# Paired low-pressure training data for the 128-root predictive timing heads.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_v3/qwen35-native-reactive-overlapped-128root-train-r0/runtime_workload_manifest.json"
EXPECTED_SHA256="65c4b4fa58708e9bb516e6e31703bf51f0c06f3c17fae5c3ca2790d4780e62be"
OUT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_tool_join_low_pressure_train20_20260927_v1}"
ARTIFACT="$ROOT/artifacts/qwen35_tool_window_100ms_train128_20260927_v1/manifest.json"
MIN_AVAILABLE_KIB=$((60 * 1024 * 1024))

if [[ ! -f "$SOURCE" || ! -f "$ARTIFACT" || -e "$OUT" ]]; then
  printf 'Missing frozen input or output already exists: %s\n' "$OUT" >&2
  exit 1
fi
printf '%s  %s\n' "$EXPECTED_SHA256" "$SOURCE" | sha256sum --check --status
available_kib="$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')"
if [[ ! "$available_kib" =~ ^[0-9]+$ ]] \
  || (( available_kib < MIN_AVAILABLE_KIB )); then
  printf 'Need at least 60 GiB free before collecting training data\n' >&2
  exit 1
fi

# SHA-256 of "beliefkv-low-pressure-20260927:" plus instance ID, lowest four
# per project. Interleaving avoids turning project identity into arrival order.
TASK_IDS="django__django-12143 psf__requests-1921 pydata__xarray-4094 pylint-dev__pylint-6903 pytest-dev__pytest-6197"
TASK_IDS+=" django__django-10554 psf__requests-5414 pydata__xarray-7393 pylint-dev__pylint-8898 pytest-dev__pytest-7324"
TASK_IDS+=" django__django-11333 psf__requests-2931 pydata__xarray-6599 pylint-dev__pylint-7277 pytest-dev__pytest-6202"
TASK_IDS+=" django__django-11179 psf__requests-6028 pydata__xarray-3677 pylint-dev__pylint-4604 pytest-dev__pytest-7571"

exec env \
  SOURCE_MANIFEST="$SOURCE" RUN_ROOT="$OUT" \
  TASK_IDS="$TASK_IDS" CLIENT_CONCURRENCY=4 ARMS=intent \
  TOOL_WINDOW_ARTIFACT="$ARTIFACT" \
  TOOL_WINDOW_AUDIT_MODE=training_replay \
  WORKFLOW_DEADLINE_SECONDS="${WORKFLOW_DEADLINE_SECONDS:-7200}" \
  REQUEST_TIMEOUT_SECONDS=600 \
  bash "$ROOT/scripts/run_child_intent_chunk_sphinx.sh"
