#!/usr/bin/env bash
set -euo pipefail

# Native SGLang migration smoke only. No BeliefKV scheduler/physical hooks.
MODEL_PATH="${MODEL_PATH:-/srv/ai/models/Qwen/Qwen3.5-35B-A3B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3.5-35B-A3B}"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-18000}"
MEM_FRACTION_STATIC="${MEM_FRACTION_STATIC:-0.90}"
MAX_RUNNING_REQUESTS="${MAX_RUNNING_REQUESTS:-32}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-131072}"
CHUNKED_PREFILL_SIZE="${CHUNKED_PREFILL_SIZE:-4096}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-0}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

if [[ ! -x "${PYTHON}" || ! -f "${MODEL_PATH}/config.json" ]]; then
  printf 'Missing Python or model config: %s %s\n' "${PYTHON}" "${MODEL_PATH}" >&2
  exit 2
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
export LIBRARY_PATH="${CUDA_HOME}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export CUDA_VISIBLE_DEVICES CUDA_HOME LIBRARY_PATH LD_LIBRARY_PATH
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
    --hicache-write-policy write_back
    --hicache-io-backend kernel
  )
fi
exec "${PYTHON}" -m sglang.launch_server "${server_args[@]}" "$@"
