"""Opt-in native terminal receipts carried by SGLang's existing output hook."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import os
import time

from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.replay_token_capture import _wire


RECEIPT_KEY = "beliefkv_causal_replay_receipt"
BRIDGE_ENV = "BELIEFKV_CAUSAL_REPLAY_BRIDGE"


def terminal_receipt(req) -> dict:
    if not req.finished():
        raise ValueError("native replay receipt requires a terminal request")
    completed = time.monotonic() * 1000.
    domain = local_monotonic_clock_domain()
    metadata = req.beliefkv_metadata
    replay = req.sampling_params.custom_params[PARAMETER_KEY]
    if (
        not domain or type(metadata) is not dict
        or replay["request_id"] != req.rid
    ):
        raise ValueError("native replay receipt lacks its request or clock identity")
    parameters = req.sampling_params
    stopping = {
        name: getattr(parameters, name)
        for name in ("max_new_tokens", "min_new_tokens", "ignore_eos")
    }
    for source, target in (
        ("stop_strs", "stop"), ("stop_regex_strs", "stop_regex"),
        ("stop_token_ids", "stop_token_ids"),
    ):
        values = getattr(parameters, source)
        stopping[target] = (
            None if values is None else
            sorted(values) if source == "stop_token_ids" else list(values)
        )
    output = list(req.output_ids)
    return {
        "schema_version": 1, "request_id": req.rid,
        "identity": [
            metadata[name] for name in (
                "root_workflow_id", "invocation_id", "context_id", "context_epoch",
            )
        ],
        "complete_monotonic_ms": completed, "clock_domain": domain,
        "prompt_tokens": len(req.origin_input_ids),
        "prompt_sha256": hashlib.sha256(_wire(req.origin_input_ids)).hexdigest(),
        "output_token_ids": output,
        "sampling_parameters": stopping,
        "native_finish_reason": deepcopy(req.finished_reason.to_json()),
        "session_id": getattr(req, "session_id", None),
        "session_generation": getattr(req, "session_generation", None),
        "extra_key": getattr(req, "extra_key", None),
        "cache_salt": getattr(req, "cache_salt", None),
        "completion_boundary": "native_terminal_output_emit",
        "scope": "native CPU terminal output boundary; not a CUDA kernel timestamp",
    }


@lru_cache(maxsize=None)
def replay_streamer_class(base):
    class ReplayOutputStreamer(base):
        has_additional_customized_info = True

        def build_additional_customized_info(self, reqs):
            result = dict(super().build_additional_customized_info(reqs))
            if RECEIPT_KEY in result:
                raise ValueError("native replay output key is already in use")
            values = []
            for req in reqs:
                parameters = getattr(req.sampling_params, "custom_params", None)
                if req.finished() and type(parameters) is dict and PARAMETER_KEY in parameters:
                    receipt = getattr(req, "_beliefkv_causal_replay_receipt", None)
                    if receipt is None:
                        receipt = terminal_receipt(req)
                        req._beliefkv_causal_replay_receipt = receipt
                    values.append([receipt])
                else:
                    values.append([])
            if any(values):
                result[RECEIPT_KEY] = values
            return result

    return ReplayOutputStreamer


def _streamer_hook(result, *_args, **_kwargs):
    return replay_streamer_class(result)


def register() -> None:
    setting = os.environ.get(BRIDGE_ENV, "0")
    if setting not in ("0", "1"):
        raise ValueError(f"{BRIDGE_ENV} must be 0 or 1")
    if setting == "0":
        return
    from sglang.srt.environ import envs
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    if not envs.SGLANG_ENABLE_DELAY_SAMPLE.get():
        raise ValueError(
            "causal replay requires SGLANG_ENABLE_DELAY_SAMPLE=1 so sampling "
            "observes the previous committed output while forward remains overlapped"
        )
    HookRegistry.register(
        "sglang.srt.managers.scheduler.Scheduler.get_output_streamer_class",
        _streamer_hook, HookType.AFTER,
    )
