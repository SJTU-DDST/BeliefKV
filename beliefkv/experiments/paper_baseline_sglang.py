"""Streaming and worker evidence for the common SGLang author baselines."""

from __future__ import annotations

from functools import lru_cache
import logging
import os
from pathlib import Path
import sys
import time

from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY, SAMPLING_ENV
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain


ENGINE = "sglang0520"
ARM_ENV = "BELIEFKV_PAPER_SGLANG_ARM"
MARS_AUTHOR_ENV = "BELIEFKV_MARS_AUTHOR_SOURCE"
MORI_CAP_ENV = "BELIEFKV_MORI_WRITE_BACK_CAP"
STATE_KEY = "beliefkv_paper_replay"
EMIT_KEY = "beliefkv_paper_output_emit"
TERMINAL_KEY = "beliefkv_paper_terminal"
IDENTITY_FIELDS = ("root_workflow_id", "invocation_id", "context_id", "context_epoch")
MOE_LOGGER = "sglang.srt.layers.moe.moe_runner.triton_utils.fused_moe_triton_config"
MOE_LOG_PREFIX = "Using MoE kernel config from "
_moe_files = {}


class _MoEConfigurationEvidence(logging.Handler):
    def emit(self, record):
        message = record.getMessage()
        if message.startswith(MOE_LOG_PREFIX) and message.endswith("."):
            path = Path(message[len(MOE_LOG_PREFIX):-1]).resolve()
            _moe_files[str(path)] = sha256_file(path)


def observe_moe_configuration():
    logger = logging.getLogger(MOE_LOGGER)
    if not any(isinstance(handler, _MoEConfigurationEvidence) for handler in logger.handlers):
        logger.addHandler(_MoEConfigurationEvidence())


@lru_cache(maxsize=None)
def paper_streamer_class(base):
    from beliefkv.experiments.causal_replay_native import terminal_receipt

    class PaperOutputStreamer(base):
        has_additional_customized_info = True

        def build_additional_customized_info(self, reqs):
            result = dict(super().build_additional_customized_info(reqs))
            if EMIT_KEY in result or TERMINAL_KEY in result:
                raise ValueError("paper replay output keys are already in use")
            stamp = None
            emission, terminal = [], []
            for req in reqs:
                params = getattr(req.sampling_params, "custom_params", None)
                if type(params) is not dict or PARAMETER_KEY not in params:
                    emission.append([])
                    terminal.append([])
                    continue
                if stamp is None:
                    stamp = (time.monotonic() * 1000., local_monotonic_clock_domain())
                offset = req.send_token_offset
                previous = getattr(req, "_beliefkv_paper_send_offset", 0)
                if offset < previous:
                    raise ValueError("paper replay native output offset went backwards")
                req._beliefkv_paper_send_offset = offset
                # SGLang slices and coalesces custom streaming metadata by token index.
                emission.append([stamp] * (offset - previous))
                if req.finished():
                    receipt = terminal_receipt(req)
                    receipt["complete_monotonic_ms"] = stamp[0]
                    receipt["program_id"] = getattr(req, "program_id", None)
                    receipt["job_id"] = receipt["identity"][0] if os.environ[ARM_ENV] == "mars" else None
                    terminal.append([receipt])
                else:
                    terminal.append([])
            if stamp is not None:
                result[EMIT_KEY] = emission
            if any(terminal):
                result[TERMINAL_KEY] = terminal
            return result

    return PaperOutputStreamer


def paper_streamer_hook(result, *_args, **_kwargs):
    return paper_streamer_class(result)


def paper_tokenizer_metadata(original, manager, meta_info, state, customized_info, index, finish_reason):
    if TERMINAL_KEY in customized_info:
        terminal = customized_info[TERMINAL_KEY][index]
        if terminal:
            if len(terminal) != 1 or finish_reason is None:
                raise ValueError("paper replay terminal evidence lacks a native finish")
            meta_info[TERMINAL_KEY] = terminal[0]
        # Keep the single terminal receipt out of the token-aligned metadata
        # so it survives slicing even if the final delta is empty.
        customized_info = {key: value for key, value in customized_info.items() if key != TERMINAL_KEY}
    return original(manager, meta_info, state, customized_info, index, finish_reason)


@lru_cache(maxsize=1)
def _loaded_sources():
    import sglang
    from beliefkv.experiments import causal_replay_native, causal_replay_sampling

    return {
        "engine_package": str(Path(sglang.__file__).resolve().parent),
        "adapter_sources_sha256": {
            str(Path(path).resolve()): sha256_file(Path(path))
            for path in (__file__, causal_replay_native.__file__, causal_replay_sampling.__file__)
        },
    }


def paper_worker_state(result, *_args, **_kwargs):
    arm = os.environ[ARM_ENV]
    result.internal_state[STATE_KEY] = {
        **_loaded_sources(),
        "arm": arm,
        "python": str(Path(sys.executable).resolve()),
        "clock_domain": local_monotonic_clock_domain(),
        "sampling_mode": os.environ[SAMPLING_ENV],
        "mars_author_source": os.environ.get(MARS_AUTHOR_ENV) or None,
        "moe_configurations": dict(_moe_files),
    }
