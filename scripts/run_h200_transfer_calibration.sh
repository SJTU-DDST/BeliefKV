#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s OUTPUT_ROOT\n' "$0" >&2
  exit 2
fi

output_root="$(realpath -m "$1")"
if [[ -e "${output_root}" ]]; then
  printf 'Output already exists: %s\n' "${output_root}" >&2
  exit 2
fi
mkdir -p "${output_root}"

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
python_bin="${PYTHON_BIN:-/home/longhao/miniconda3/envs/beliefkv/bin/python}"
runtime_profile="${RUNTIME_PROFILE:-${repo_root}/configs/p6/h200_bf16_v1/frozen_runtime_profile.json}"
model_path="$(jq -er '.model.path' "${runtime_profile}")"
model_name="$(jq -er '.model.served_name' "${runtime_profile}")"
port="${PORT:-18000}"
pause_file="${BELIEFKV_EXPERIMENT_PAUSE_FILE:-/tmp/beliefkv-experiments.paused}"
active_pid=""
active_pgid=""

cleanup() {
  if [[ -n "${active_pgid}" ]] && kill -0 "${active_pid}" 2>/dev/null; then
    kill -TERM -- "-${active_pgid}" 2>/dev/null || true
    wait "${active_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [[ -e "${pause_file}" ]]; then
  printf 'BeliefKV experiments are paused: %s\n' "${pause_file}" >&2
  exit 75
fi
if [[ ! -x "${python_bin}" || ! -f "${model_path}/config.json" || ! -f "${runtime_profile}" ]]; then
  printf 'Missing Python environment, model path, or runtime profile\n' >&2
  exit 2
fi

cd "${repo_root}"
for target_tokens in 65536 131072 196608; do
  prompt_words=$((target_tokens - 64))
  for repeat in 0 1 2; do
    run_id="t${target_tokens}-r${repeat}"
    gate_id="h200-transfer-${run_id}"
    run_dir="${output_root}/${run_id}"
    server_dir="${run_dir}/server"
    mkdir -p "${server_dir}"

    "${python_bin}" scripts/prepare_deepagents_server_config.py       --server-dir "${server_dir}"       --kv-bytes-per-token 98304       --queue-service-observer       --enable-observed-admission       --enable-online-joint       --enable-running-retraction       --enable-restore-micro-gate       --restore-micro-gate-id "${gate_id}"       --restore-micro-gate-min-private-mib 64       --disable-snapshot-persistence

    CUDA_VISIBLE_DEVICES=0 PORT="${port}" SLEEP_ON_IDLE=1       setsid scripts/launch_deepagents_swebench_server.sh       --runtime-profile "${runtime_profile}" "${server_dir}" &
    active_pid=$!
    active_pgid="$(ps -o pgid= -p "${active_pid}" | tr -d ' ')"

    healthy=0
    for _ in $(seq 1 60); do
      if curl -fsS --max-time 3         "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
        healthy=1
        break
      fi
      if ! kill -0 "${active_pid}" 2>/dev/null; then
        break
      fi
      sleep 5
    done
    if [[ "${healthy}" != 1 ]]; then
      tail -120 "${server_dir}/server.log" >&2 || true
      exit 1
    fi

    "${python_bin}" scripts/run_restore_micro_gate.py       --output-dir "${run_dir}/client"       --runtime-audit "${server_dir}/runtime_audit.jsonl"       --gate-id "${gate_id}"       --base-url "http://127.0.0.1:${port}/v1"       --model "${model_name}"       --expected-model-path "${model_path}"       --expected-weight-dtype bfloat16       --expected-kv-dtype bfloat16       --victim-prompt-words "${prompt_words}"       --anchor-prompt-words "${prompt_words}"       --replacement-prompt-words 65536       --holder-output-tokens 1024       --replacement-output-tokens 256       --required-max-running-requests "$(jq -er '.runtime.max_running_requests' "${runtime_profile}")"       --service-wait-seconds 600       --request-timeout-seconds 1800

    scripts/stop_deepagents_swebench_server.sh "${server_dir}" "${active_pid}"
    wait "${active_pid}" 2>/dev/null || true
    active_pid=""
    active_pgid=""

    "${python_bin}" scripts/verify_restore_micro_gate.py       --runtime-audit "${server_dir}/runtime_audit.jsonl"       --runtime-summary "${server_dir}/latest_runtime_summary.json"       --gate-id "${gate_id}"       --output "${run_dir}/restore_gate_analysis.json"
  done
done

trap - EXIT INT TERM
printf 'H200 transfer calibration completed: %s\n' "${output_root}"
