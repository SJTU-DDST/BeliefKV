#!/usr/bin/env bash
set -euo pipefail

# Native fixed-size HiCache by default; HICACHE_SIZE_GB=0 disables Host caching.
# Source checkout + explicit CLI flag can enable admission-only.
MODEL_PATH="${MODEL_PATH:-/srv/ai/models/Qwen/Qwen3.5-35B-A3B}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen3.5-35B-A3B}"
MODEL_CACHE_LAYOUT="${MODEL_CACHE_LAYOUT:-hybrid}"
REASONING_PARSER="${REASONING_PARSER-qwen3}"
PYTHON="${PYTHON:-/home/longhao/miniconda3/envs/beliefkv-next/bin/python}"
SGLANG_SOURCE_CHECKOUT="${SGLANG_SOURCE_CHECKOUT:-}"
SGLANG_SOURCE_BASE_COMMIT="${SGLANG_SOURCE_BASE_COMMIT:-94602c9c2b7cbdb8efd5c52802dac6a1c180089e}"
SGLANG_PATCH_FLAVOR="${SGLANG_PATCH_FLAVOR:-staging}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-18000}"
MEM_FRACTION_STATIC="${MEM_FRACTION_STATIC:-0.90}"
MAX_RUNNING_REQUESTS="${MAX_RUNNING_REQUESTS:-48}"
SERVER_RANDOM_SEED="${SERVER_RANDOM_SEED:-21}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-131072}"
CHUNKED_PREFILL_SIZE="${CHUNKED_PREFILL_SIZE:-4096}"
HICACHE_SIZE_GB="${HICACHE_SIZE_GB:-200}"
BELIEFKV_FULL_MAMBA_HOST_SPLIT="${BELIEFKV_FULL_MAMBA_HOST_SPLIT:-80:20}"
if [[ "$MODEL_CACHE_LAYOUT" == full_only ]]; then
  if ! jq --exit-status \
    '.model_type == "qwen3_moe" and .use_sliding_window == false and .sliding_window == null' \
    "$MODEL_PATH/config.json" >/dev/null; then
    printf 'FULL-only launch requires the declared Qwen3 MoE model config\n' >&2
    exit 2
  fi
  BELIEFKV_FULL_MAMBA_HOST_SPLIT=auto
elif [[ "$MODEL_CACHE_LAYOUT" != hybrid ]]; then
  printf 'Unknown MODEL_CACHE_LAYOUT: %s\n' "$MODEL_CACHE_LAYOUT" >&2
  exit 2
fi
if [[ "${BELIEFKV_FULL_MAMBA_HOST_SPLIT}" == auto ]]; then
  unset BELIEFKV_FULL_MAMBA_HOST_SPLIT
fi
HOST_NUMA_NODE="${HOST_NUMA_NODE:-1}"
HOST_NUMA_MEMORY_POLICY="${HOST_NUMA_MEMORY_POLICY:-bind}"
HICACHE_WRITE_POLICY="${HICACHE_WRITE_POLICY:-write_back}"
ENABLE_SESSION_RADIX_CACHE="${ENABLE_SESSION_RADIX_CACHE:-0}"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
BELIEFKV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! "$SERVER_RANDOM_SEED" =~ ^(0|[1-9][0-9]{0,9})$ ]] \
    || (( SERVER_RANDOM_SEED > 4294967295 )); then
  printf 'SERVER_RANDOM_SEED must be an integer from 0 to 4294967295\n' >&2
  exit 2
fi

if [[ "${SGLANG_PATCH_FLAVOR}" == native_session_close \
    && -z "${SGLANG_SOURCE_CHECKOUT}" ]]; then
  printf 'native_session_close requires SGLANG_SOURCE_CHECKOUT\n' >&2
  exit 2
fi
if [[ "${SGLANG_NATIVE_SESSION_CLOSE_RECLAIM:-0}" == "1" \
    && "${SGLANG_PATCH_FLAVOR}" != native_session_close ]]; then
  printf 'SGLANG_NATIVE_SESSION_CLOSE_RECLAIM requires native_session_close patch flavor\n' >&2
  exit 2
fi

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
admission_requested=0
event_socket_requested=0
confirmed_join_requested=0
for arg in "$@"; do
  case "${arg}" in
    --enable-beliefkv-admission) admission_requested=1 ;;
    --beliefkv-event-socket-path|--beliefkv-event-socket-path=*)
      event_socket_requested=1 ;;
    --beliefkv-confirmed-join-canary) confirmed_join_requested=1 ;;
  esac
done
if [[ ! ${BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH+x} ]]; then
  BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=0
  if (( admission_requested && event_socket_requested && ! confirmed_join_requested )) \
      && [[ -n "${SGLANG_SOURCE_CHECKOUT}" ]] \
      && [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" ]]; then
    BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH=1
  fi
fi
export BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH
if [[ ! ${BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY+x} ]]; then
  BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=0
  if (( admission_requested && event_socket_requested && ! confirmed_join_requested )) \
      && [[ -n "${SGLANG_SOURCE_CHECKOUT}" ]] \
      && [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" ]]; then
    BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY=1
  fi
fi
export BELIEFKV_ENABLE_FINAL_STAGE_PRIORITY
if [[ "${BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH}" == "1" ]]; then
  if [[ -z "${SGLANG_SOURCE_CHECKOUT}" ]] \
      || (( admission_requested == 0 )) \
      || (( event_socket_requested == 0 || confirmed_join_requested )) \
      || [[ "${ENABLE_SESSION_RADIX_CACHE}" != "1" ]]; then
    printf 'Final stage prefetch requires patched SGLang, admission, event socket, session radix cache, and no confirmed JOIN canary\n' >&2
    exit 2
  fi
elif [[ "${BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH}" != "0" ]]; then
  printf 'BELIEFKV_ENABLE_FINAL_STAGE_PREFETCH must be 0 or 1\n' >&2
  exit 2
fi
if [[ ${BELIEFKV_ADMISSION_TELEMETRY_DIR+x} ]]; then
  if [[ -z "${SGLANG_SOURCE_CHECKOUT}" ]] \
      || [[ ${BELIEFKV_NATIVE_TELEMETRY_DIR+x} ]] \
      || (( admission_requested == 0 )); then
    printf 'Admission telemetry requires patched SGLang, admission enabled, and no reactive telemetry\n' >&2
    exit 2
  fi
  if [[ ! -d "${BELIEFKV_ADMISSION_TELEMETRY_DIR}" || ! -w "${BELIEFKV_ADMISSION_TELEMETRY_DIR}" ]]; then
    printf 'Admission telemetry directory must exist and be writable: %s\n' "${BELIEFKV_ADMISSION_TELEMETRY_DIR}" >&2
    exit 2
  fi
  export BELIEFKV_ADMISSION_TELEMETRY_DIR
elif [[ ${BELIEFKV_NATIVE_TELEMETRY_DIR+x} ]] && (( admission_requested )); then
  printf 'Reactive telemetry cannot be used with BeliefKV admission\n' >&2
  exit 2
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
if [[ "${HICACHE_WRITE_POLICY}" != "write_back" && "${HICACHE_WRITE_POLICY}" != "write_through" && "${HICACHE_WRITE_POLICY}" != "write_through_selective" ]] \
    || [[ "${ENABLE_SESSION_RADIX_CACHE}" != "0" && "${ENABLE_SESSION_RADIX_CACHE}" != "1" ]]; then
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
if (( HICACHE_SIZE_GB > 0 )) || [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" ]]; then
  if [[ "$HOST_NUMA_MEMORY_POLICY" != bind && "$HOST_NUMA_MEMORY_POLICY" != preferred ]]; then
    printf 'HOST_NUMA_MEMORY_POLICY must be bind or preferred\n' >&2
    exit 2
  fi
  if ! command -v numactl >/dev/null; then
    printf 'numactl is required for NUMA-local HiCache\n' >&2
    exit 2
  fi
  if (( HICACHE_SIZE_GB > 0 )); then
    "${PYTHON}" "${BELIEFKV_ROOT}/scripts/preflight_qwen35_host_pool.py" \
      --memory-policy "$HOST_NUMA_MEMORY_POLICY" \
      --node "${HOST_NUMA_NODE}" --size-gb "${HICACHE_SIZE_GB}"
  fi
  # SGLang's automatic worker binding otherwise replaces this policy with membind.
  export SGLANG_AUTO_NUMA_BIND=0
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
  if [[ ! "${SGLANG_SOURCE_BASE_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
      || [[ ! -f "${SGLANG_SOURCE_CHECKOUT}/python/sglang/srt/managers/scheduler.py" ]] \
      || [[ "$(git -C "${SGLANG_SOURCE_CHECKOUT}" rev-parse HEAD)" != "${SGLANG_SOURCE_BASE_COMMIT}" ]]; then
    printf 'Expected patched v0.5.20 checkout: %s\n' "${SGLANG_SOURCE_CHECKOUT}" >&2
    exit 2
  fi
  case "${SGLANG_PATCH_FLAVOR}" in
    staging)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-v0.5.20-beliefkv-staging.patch"
      ;;
    native_session_close)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-v0.5.20-native-session-close-staging.patch"
      ;;
    confirmed_join_canary)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-v0.5.20-beliefkv-confirmed-join-canary.patch"
      ;;
    writeback_prepare)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-v0.5.20-beliefkv-writeback-prepare.patch"
      ;;
    layer_handoff)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-layer-handoff-runtime-20261027.patch"
      ;;
    layer_handoff_groups)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-layer-handoff-groups-runtime-20261028.patch"
      ;;
    sibling_capacity)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-sibling-capacity-runtime-20261028.patch"
      ;;
    demand_prefill_pipeline)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-demand-prefill-pipeline-runtime-20261028.patch"
      ;;
    tokencake_scheduler)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-tokencake-scheduler-runtime-20261028.patch"
      ;;
    agentix_common)
      sglang_patch="${BELIEFKV_ROOT}/patches/sglang-agentix-common-runtime-20261029.patch"
      ;;
    *)
      printf 'Unknown SGLang patch flavor: %s\n' "${SGLANG_PATCH_FLAVOR}" >&2
      exit 2
      ;;
  esac
  git -C "${SGLANG_SOURCE_CHECKOUT}" apply --reverse --check "${sglang_patch}"
  printf 'SGLang source patch: %s sha256=%s\n' \
    "${SGLANG_PATCH_FLAVOR}" "$(sha256sum "${sglang_patch}" | cut -d' ' -f1)"
  export PYTHONPATH="${SGLANG_SOURCE_CHECKOUT}/python:${BELIEFKV_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
  "${PYTHON}" -c \
    'import pathlib,sglang; assert pathlib.Path(sglang.__file__).resolve().is_relative_to(pathlib.Path(__import__("sys").argv[1]).resolve())' \
    "${SGLANG_SOURCE_CHECKOUT}/python"
fi
export LIBRARY_PATH="${CUDA_HOME}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export CUDA_VISIBLE_DEVICES CUDA_HOME LIBRARY_PATH LD_LIBRARY_PATH
if [[ -n "${BELIEFKV_FULL_MAMBA_HOST_SPLIT:-}" ]]; then
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
  --random-seed "${SERVER_RANDOM_SEED}"
  --tool-call-parser qwen3_coder
  --enable-metrics
)
if [[ -n "$REASONING_PARSER" ]]; then
  server_args+=(--reasoning-parser "$REASONING_PARSER")
fi
if [[ "${HICACHE_SIZE_GB}" -gt 0 ]]; then
  server_args+=(
    --enable-hierarchical-cache
    --hicache-size "${HICACHE_SIZE_GB}"
    --hicache-write-policy "${HICACHE_WRITE_POLICY}"
    --hicache-io-backend kernel
    --hicache-mem-layout "${HICACHE_MEM_LAYOUT:-page_first}"
  )
fi
if [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" ]]; then
  server_args+=(--enable-session-radix-cache)
fi
if (( HICACHE_SIZE_GB > 0 )) || [[ "${ENABLE_SESSION_RADIX_CACHE}" == "1" ]]; then
  memory_args=("--membind=${HOST_NUMA_NODE}")
  if [[ "$HOST_NUMA_MEMORY_POLICY" == preferred ]]; then
    memory_args=("--preferred=${HOST_NUMA_NODE}")
  fi
  exec numactl --cpunodebind="${HOST_NUMA_NODE}" "${memory_args[@]}" \
    "${PYTHON}" -m sglang.launch_server "${server_args[@]}" "$@"
fi
exec "${PYTHON}" -m sglang.launch_server "${server_args[@]}" "$@"
