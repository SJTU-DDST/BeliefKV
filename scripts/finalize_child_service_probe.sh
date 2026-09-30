#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 || ! "$2" =~ ^[1-9][0-9]*$ ]]; then
  printf 'Usage: %s <run-directory> <running-probe-pid>\n' "$0" >&2
  exit 2
fi

RUN="$(realpath "$1")"
PID="$2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"

shopt -s nullglob
clients=()
for candidate in "$RUN"/client_*; do
  if [[ -d "$candidate/workflows" ]]; then
    clients+=("$candidate")
  fi
done
if [[ ${#clients[@]} -ne 1 ]]; then
  printf 'Expected exactly one client workflow directory: %s\n' "$RUN" >&2
  exit 2
fi
if [[ -e "$RUN/final_stage_service_audit.json" \
   || -e "$RUN/final_stage_service_labels.jsonl" \
   || -e "$RUN/child_return_content_service_project_loo.json" ]]; then
  printf 'Final evaluation artifacts already exist: %s\n' "$RUN" >&2
  exit 2
fi

while kill -0 "$PID" 2>/dev/null \
  && [[ "$(ps -o stat= -p "$PID" 2>/dev/null)" != Z* ]]; do
  sleep 30
done

"$PYTHON" "$ROOT/scripts/audit_native_final_stage_notice.py" \
  "${clients[0]}" \
  --server-events "$RUN/server/runtime_events.sglang.jsonl" \
  --service-audit "$RUN/server/runtime_audit.jsonl" \
  --output "$RUN/final_stage_service_audit.json" \
  --rows-output "$RUN/final_stage_service_labels.jsonl"

"$PYTHON" "$ROOT/scripts/evaluate_child_return_content_service.py" \
  "$RUN" --output "$RUN/child_return_content_service_project_loo.json"

printf 'Child RETURN service audit and held-out evaluation: %s\n' "$RUN"
