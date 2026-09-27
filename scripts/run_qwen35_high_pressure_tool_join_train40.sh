#!/usr/bin/env bash
# High-concurrency, same-project training replay for early JOIN and tool timing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/experiments/raw/qwen35_native_reactive_overlapped_128root_train_20260923_v3/qwen35-native-reactive-overlapped-128root-train-r0/runtime_workload_manifest.json"
EXPECTED_SHA256="65c4b4fa58708e9bb516e6e31703bf51f0c06f3c17fae5c3ca2790d4780e62be"
OUT="${RUN_ROOT:-$ROOT/experiments/raw/qwen35_tool_join_high_pressure_train40_20260927_v1}"
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

# Frozen manifest IDs only; select eight per project without reading outcomes.
mapfile -t ids < <(
  jq -r '
    [.workloads[].instance_id
      | select(test("^(django|psf|pydata|pylint-dev|pytest-dev)__"))]
    | sort | group_by(split("__")[0]) | map(.[0:8])
    | transpose | flatten | .[]
  ' "$SOURCE"
)
if (( ${#ids[@]} != 40 )); then
  printf 'Expected forty balanced training tasks; got %s\n' "${#ids[@]}" >&2
  exit 1
fi
task_ids="${ids[*]}"

exec env \
  SOURCE_MANIFEST="$SOURCE" RUN_ROOT="$OUT" \
  TASK_IDS="$task_ids" CLIENT_CONCURRENCY=40 ARMS=intent \
  TOOL_WINDOW_ARTIFACT="$ARTIFACT" \
  TOOL_WINDOW_AUDIT_MODE=training_replay \
  WORKFLOW_DEADLINE_SECONDS="${WORKFLOW_DEADLINE_SECONDS:-7200}" \
  REQUEST_TIMEOUT_SECONDS=600 \
  bash "$ROOT/scripts/run_child_intent_chunk_sphinx.sh"
