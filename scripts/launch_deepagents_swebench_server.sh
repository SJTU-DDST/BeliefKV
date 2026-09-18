#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || "$1" != "--runtime-profile" ]]; then
  printf 'Usage: %s --runtime-profile PROFILE SERVER_DIR [SGLANG_ARGS...]\n' "$0" >&2
  exit 2
fi

REPOSITORY_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_PROFILE="$(realpath "$2")"
SERVER_DIR="$(realpath "$3")"
shift 3
CONFIG_PATH="${SERVER_DIR}/beliefkv_config.json"
CONTRACT_PATH="${SERVER_DIR}/runtime_profile_contract.json"
SGLANG_ROOT="${SGLANG_ROOT:-${REPOSITORY_ROOT}/third_party/sglang}"
PROFILE_PYTHON="${PROFILE_PYTHON:-/home/longhao/miniconda3/envs/beliefkv/bin/python}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  printf 'Missing server config: %s\n' "${CONFIG_PATH}" >&2
  exit 2
fi
if [[ ! -f "${RUNTIME_PROFILE}" ]]; then
  printf 'Missing runtime profile: %s\n' "${RUNTIME_PROFILE}" >&2
  exit 2
fi
if [[ ! -x "${PROFILE_PYTHON}" ]]; then
  printf 'Missing profile validator Python: %s\n' "${PROFILE_PYTHON}" >&2
  exit 2
fi

immutable_args=(
  --model-path --served-model-name --tensor-parallel-size --dtype
  --kv-cache-dtype --page-size --context-length --max-total-tokens
  --mem-fraction-static --chunked-prefill-size --max-prefill-tokens
  --max-running-requests
  --cuda-graph-max-bs --hicache-size --hicache-write-policy
  --hicache-io-backend --hicache-mem-layout
)
for argument in "$@"; do
  for immutable in "${immutable_args[@]}"; do
    if [[ "${argument}" == "${immutable}" || "${argument}" == "${immutable}="* ]]; then
      printf 'Runtime profile owns immutable argument: %s\n' "${argument}" >&2
      exit 2
    fi
  done
done

profile_value() {
  jq -er "$1" "${RUNTIME_PROFILE}"
}

export MODEL_PATH="$(profile_value '.model.path')"
export SERVED_MODEL_NAME="$(profile_value '.model.served_name')"
export WEIGHT_DTYPE="$(profile_value '.model.weight_dtype')"
export KV_CACHE_DTYPE="$(profile_value '.runtime.kv_cache_cli_dtype')"
export PAGE_SIZE="$(profile_value '.runtime.page_size')"
export CONTEXT_LENGTH="$(profile_value '.model.context_length')"
export MAX_TOTAL_TOKENS="$(profile_value '.capacity.max_total_tokens')"
export MEM_FRACTION_STATIC="$(profile_value '.runtime.mem_fraction_static')"
export MAX_RUNNING_REQUESTS="$(profile_value '.runtime.max_running_requests')"
export CHUNKED_PREFILL_SIZE="$(profile_value '.runtime.chunked_prefill_size')"
export MAX_PREFILL_TOKENS="$(profile_value '.runtime.max_prefill_tokens // 16384')"
export CUDA_GRAPH_MAX_BS="$(profile_value '.runtime.cuda_graph_max_bs')"
export HICACHE_SIZE_GB="$(profile_value '.runtime.hicache_size_gib')"
export HICACHE_WRITE_POLICY="$(profile_value '.runtime.hicache_write_policy')"
export HICACHE_IO_BACKEND="$(profile_value '.runtime.hicache_io_backend')"
export HICACHE_MEM_LAYOUT="$(profile_value '.runtime.hicache_mem_layout')"
export TENSOR_PARALLEL_SIZE="$(profile_value '.runtime.tensor_parallel_size')"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export PORT="${PORT:-18000}"
export HOST="${HOST:-127.0.0.1}"
export SLEEP_ON_IDLE="${SLEEP_ON_IDLE:-1}"

if ss -H -ltn "sport = :${PORT}" | grep -q .; then
  printf 'Server port is already occupied: %s:%s\n' "${HOST}" "${PORT}" >&2
  exit 2
fi

mkdir -p "${SERVER_DIR}"
printf '%s\n' "$$" >"${SERVER_DIR}/server.pid"
server_start_ticks="$(awk '{print $22}' "/proc/$$/stat")"
server_pgid="$(ps -o pgid= -p $$ | tr -d ' ')"
printf '{"linux_start_time_ticks":%s,"pgid":%s,"pid":%s,"schema_version":1}\n' \
  "${server_start_ticks}" "${server_pgid}" "$$" \
  >"${SERVER_DIR}/server.pid.json"

"${PROFILE_PYTHON}" "${REPOSITORY_ROOT}/scripts/validate_runtime_profile.py" \
  --phase preflight \
  --runtime-profile "${RUNTIME_PROFILE}" \
  --beliefkv-config "${CONFIG_PATH}" \
  --sglang-root "${SGLANG_ROOT}" \
  --output "${CONTRACT_PATH}"

sleep_args=()
if [[ "${SLEEP_ON_IDLE}" == "1" ]]; then
  sleep_args+=(--sleep-on-idle)
fi

child_pid=""
terminate_child() {
  if [[ -n "${child_pid}" ]] && kill -0 "${child_pid}" 2>/dev/null; then
    kill -TERM "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
  fi
}
trap terminate_child INT TERM EXIT

"$(dirname "$0")/launch_qwen3_coder_qwencode_smoke.sh" \
  --enable-hierarchical-cache \
  --hicache-size "${HICACHE_SIZE_GB}" \
  --hicache-write-policy "${HICACHE_WRITE_POLICY}" \
  --hicache-io-backend "${HICACHE_IO_BACKEND}" \
  --hicache-mem-layout "${HICACHE_MEM_LAYOUT}" \
  --enable-beliefkv \
  --beliefkv-config "${CONFIG_PATH}" \
  "${sleep_args[@]}" \
  "$@" \
  >"${SERVER_DIR}/server.log" 2>&1 &
child_pid=$!

contract_base_url="http://127.0.0.1:${PORT}"
if ! "${PROFILE_PYTHON}" "${REPOSITORY_ROOT}/scripts/validate_runtime_profile.py" \
  --phase server \
  --runtime-profile "${RUNTIME_PROFILE}" \
  --beliefkv-config "${CONFIG_PATH}" \
  --sglang-root "${SGLANG_ROOT}" \
  --base-url "${contract_base_url}" \
  --wait-seconds "${SERVER_STARTUP_TIMEOUT_SECONDS:-1200}" \
  --output "${CONTRACT_PATH}"; then
  printf 'SGLang runtime-profile contract failed: %s\n' "${CONTRACT_PATH}" >&2
  terminate_child
  exit 1
fi

if wait "${child_pid}"; then
  return_code=0
else
  return_code=$?
fi
child_pid=""
trap - INT TERM EXIT
exit "${return_code}"
