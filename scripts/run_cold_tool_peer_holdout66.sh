#!/usr/bin/env bash
# Frozen project-disjoint collection for cold-tool timing; no physical actions.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/experiments/raw/qwen35_native_reactive_calibration_timing_v3_20260925/qwen35-native-reactive-calibration-66root-r0/runtime_workload_manifest.json"
OUT="$ROOT/experiments/raw/qwen35_cold_tool_peer_holdout_66root_v1"
SOURCE_SHA256="fbce7fb3175d73ac0d2b881e3e16a669c32cc3063b56ca25a883eb606d07e2cb"

if [[ ! -f "$SOURCE" ]]; then
  printf 'Missing frozen holdout manifest: %s\n' "$SOURCE" >&2
  exit 1
fi
if [[ -e "$OUT" ]]; then
  printf 'Refusing to overwrite holdout output: %s\n' "$OUT" >&2
  exit 1
fi
if [[ "$(sha256sum "$SOURCE" | cut -d' ' -f1)" != "$SOURCE_SHA256" ]]; then
  printf 'Frozen holdout manifest SHA-256 changed\n' >&2
  exit 1
fi
if ! jq -e '
  .workloads as $items
  | ($items | length) == 66
    and ([$items[].instance_id] | unique | length) == 66
    and ([$items[].instance_id
          | select(startswith("astropy__"))] | length) == 22
    and ([$items[].instance_id
          | select(startswith("sphinx-doc__"))] | length) == 44
' "$SOURCE" >/dev/null; then
  printf 'Frozen holdout manifest identity or project counts changed\n' >&2
  exit 1
fi

TASK_IDS="$(jq -r '[.workloads[].instance_id] | join(" ")' "$SOURCE")"
export TASK_IDS
export SOURCE_MANIFEST="$SOURCE"
export RUN_ROOT="$OUT"
export ARMS=intent
export BELIEFKV_COMMAND_STRUCTURE_SHADOW=1
export CLIENT_CONCURRENCY=66
export WORKFLOW_ARRIVAL_BATCH_SIZE=33
export WORKFLOW_ARRIVAL_BATCH_INTERVAL_MS=60000
export REQUEST_TIMEOUT_SECONDS=7200
export WORKFLOW_DEADLINE_SECONDS=7200
export PORT=18001

exec bash "$ROOT/scripts/run_child_intent_chunk_sphinx.sh"
