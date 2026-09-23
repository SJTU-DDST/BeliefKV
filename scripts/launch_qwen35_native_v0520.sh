#!/usr/bin/env bash
set -euo pipefail

# Native fixed-size HiCache by default; HICACHE_SIZE_GB=0 retains the no-host
# smoke mode. Source checkout + explicit CLI flag can enable admission-only.
MODEL_PATH="${MODEL_PATH:-/srv/ai/models/Qwen/Qwen3.5-35B-A3B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3.5-35B-A3B}"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
SGLANG_SOURCE_CHECKOUT="${SGLANG_SOURCE_CHECKOUT:-}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-18000}"
MEM_FRACTION_STATIC="${MEM_FRACTION_STATIC:-0.90}"
MAX_RUNNING_REQUESTS="${MAX_RUNNING_REQUESTS:-48}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-131072}"
CHUNKED_PREFILL_SIZE="${CHUNKED_PREFILL_SIZE:-4096}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"
BELIEFKV_FULL_MAMBA_HOST_SPLIT="${BELIEFKV_FULL_MAMBA_HOST_SPLIT:-}"
HOST_NUMA_NODE="${HOST_NUMA_NODE:-1}"
HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"
ENABLE_SESSION_RADIX_CACHE="${ENABLE_SESSION_RADIX_CACHE:-0}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
BELIEFKV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ${BELIEFKV_NATIVE_TELEMETRY_DIR+x} ]]; then
  if [[ -z "${SGLANG_SOURCE_CHECKOUT}" ]]; then
    printf 'BELIEFKV_NATIVE_TELEMETRY_DIR requires SGLANG_SOURCE_CHECKOUT (patched v0.5.20)\n' >&2
    exit 2
  fi
  if [[ ! -d "${BELIEFKV_NATIVE_TELEMETRY_DIR}" || ! -w "${BELIEFKV_NATIVE_TELEMETRY_DIR}" ]]; then
    printf 'Telemetry directory must exist and be writable: %s\n' "${BELIEFKV_NATIVE_TELEMETRY_DIR}" >&2
    exit 2
  fi
  export BELIEFKV_NATIVE_TELEMETRY_DIR
fi
if [[ ! -x "${PYTHON}" || ! -f "${MODEL_PATH}/config.json" ]]; then
  printf 'Missing Python or model config: %s %s\n' "${PYTHON}" "${MODEL_PATH}" >&2
  exit 2
fi
if ! [[ "${HICACHE_SIZE_GB}" =~ ^(0|[1-9][0-9]{0,2})$ ]] \
    || (( HICACHE_SIZE_GB > 200 )); then
  printf 'HICACHE_SIZE_GB must be an integer from 0 to 200 (total FULL+MAMBA)\n' >&2
  exit 2
fi
if [[ "${HICACHE_WRITE_POLICY}" != "write_back" && "${HICACHE_WRITE_POLICY}" != "write_through" ]] \
    || [[ "${ENABLE_SESSION_RADIX_CACHE}" != "0" && "${ENABLE_SESSION_RADIX_CACHE}" != "1" ]] \
    || [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" && "${HICACHE_SIZE_GB}" -le 0 ]]; then
  printf 'Invalid HiCache write/session configuration\n' >&2
  exit 2
fi
for arg in "$@"; do
  case "${arg}" in
    --hicache-size|--hicache-size=*|--hicache-ratio|--hicache-ratio=*)
      printf 'HiCache capacity overrides must use HICACHE_SIZE_GB\n' >&2
      exit 2
      ;;
    --enable-hierarchical-cache)
      if (( HICACHE_SIZE_GB == 0 )); then
        printf 'HiCache requires HICACHE_SIZE_GB > 0 for NUMA binding\n' >&2
        exit 2
      fi
      ;;
  esac
done
if (( HICACHE_SIZE_GB > 0 )); then
  if ! command -v numactl >/dev/null; then
    printf 'numactl is required for NUMA-local HiCache\n' >&2
    exit 2
  fi
  "${PYTHON}" "${BELIEFKV_ROOT}/scripts/preflight_qwen35_host_pool.py" \
    --node "${HOST_NUMA_NODE}" --size-gb "${HICACHE_SIZE_GB}"
fi
CUDA_HOME="${CUDA_HOME:-$(dirname "$(dirname "${PYTHON}")")/lib/python3.11/site-packages/nvidia/cu13}"
if [[ ! -x "${CUDA_HOME}/bin/nvcc" ]]; then
  printf 'CUDA 13 nvcc missing from migration environment: %s\n' "${CUDA_HOME}" >&2
  exit 2
fi
if [[ ! -f "${CUDA_HOME}/lib/libcudart.so.13" ]]; then
  printf 'CUDA 13 runtime library missing from migration environment: %s\n' "${CUDA_HOME}/lib" >&2
  exit 2
fi
if [[ ! -e "${CUDA_HOME}/lib/libcudart.so" ]]; then
  if [[ -L "${CUDA_HOME}/lib/libcudart.so" ]]; then
    printf 'Broken CUDA runtime linker alias: %s\n' "${CUDA_HOME}/lib/libcudart.so" >&2
    exit 2
  fi
  ln -s libcudart.so.13 "${CUDA_HOME}/lib/libcudart.so"
fi
"${PYTHON}" -c 'import importlib.metadata as m; assert m.version("sglang") == "0.5.20"'
if [[ -n "${SGLANG_SOURCE_CHECKOUT}" ]]; then
  SGLANG_SOURCE_CHECKOUT="$(realpath "${SGLANG_SOURCE_CHECKOUT}")"
  if [[ ! -f "${SGLANG_SOURCE_CHECKOUT}/python/sglang/srt/managers/scheduler.py" ]] \
      || [[ "$(git -C "${SGLANG_SOURCE_CHECKOUT}" rev-parse HEAD)" != "94602c9c2b7cbdb8efd5c52802dac6a1c180089e" ]]; then
    printf 'Expected patched v0.5.20 checkout: %s\n' "${SGLANG_SOURCE_CHECKOUT}" >&2
    exit 2
  fi
  git -C "${SGLANG_SOURCE_CHECKOUT}" apply --reverse --check \
    "${BELIEFKV_ROOT}/patches/sglang-v0.5.20-beliefkv-staging.patch"
  export PYTHONPATH="${SGLANG_SOURCE_CHECKOUT}/python:${BELIEFKV_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
  "${PYTHON}" -c \
    'import pathlib,sglang; assert pathlib.Path(sglang.__file__).resolve().is_relative_to(pathlib.Path(__import__("sys").argv[1]).resolve())' \
    "${SGLANG_SOURCE_CHECKOUT}/python"
fi
export LIBRARY_PATH="${CUDA_HOME}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export CUDA_VISIBLE_DEVICES CUDA_HOME LIBRARY_PATH LD_LIBRARY_PATH
if [[ -n "${BELIEFKV_FULL_MAMBA_HOST_SPLIT}" ]]; then
  if [[ ! "${BELIEFKV_FULL_MAMBA_HOST_SPLIT}" =~ ^[0-9]+:[0-9]+$ ]]; then
    printf 'BELIEFKV_FULL_MAMBA_HOST_SPLIT must use FULL:MAMBA integer percentages\n' >&2
    exit 2
  fi
  full_share="${BELIEFKV_FULL_MAMBA_HOST_SPLIT%%:*}"
  mamba_share="${BELIEFKV_FULL_MAMBA_HOST_SPLIT##*:}"
  if (( full_share <= 0 || mamba_share <= 0 || full_share + mamba_share != 100 )); then
    printf 'BELIEFKV_FULL_MAMBA_HOST_SPLIT percentages must be positive and sum to 100\n' >&2
    exit 2
  fi
  export BELIEFKV_FULL_MAMBA_HOST_SPLIT
fi
export PYTHONUNBUFFERED=1

server_args=(
  --model-path "${MODEL_PATH}"
  --served-model-name "${SERVED_MODEL_NAME}"
  --host "${HOST}"
  --port "${PORT}"
  --dtype bfloat16
  --kv-cache-dtype bfloat16
  --tensor-parallel-size 1
  --context-length "${CONTEXT_LENGTH}"
  --mem-fraction-static "${MEM_FRACTION_STATIC}"
  --chunked-prefill-size "${CHUNKED_PREFILL_SIZE}"
  --max-running-requests "${MAX_RUNNING_REQUESTS}"
  --reasoning-parser qwen3
  --tool-call-parser qwen3_coder
  --enable-metrics
)
if [[ "${HICACHE_SIZE_GB}" -gt 0 ]]; then
  server_args+=(
    --enable-hierarchical-cache
    --hicache-size "${HICACHE_SIZE_GB}"
    --hicache-write-policy "${HICACHE_WRITE_POLICY}"
    --hicache-io-backend kernel
  )
fi
if [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" ]]; then
  server_args+=(--enable-session-radix-cache)
fi
if (( HICACHE_SIZE_GB > 0 )); then
  exec numactl --cpunodebind="${HOST_NUMA_NODE}" --membind="${HOST_NUMA_NODE}" \
    "${PYTHON}" -m sglang.launch_server "${server_args[@]}" "$@"
fi
exec "${PYTHON}" -m sglang.launch_server "${server_args[@]}" "$@"
