"""Token replay hooks called by the pinned MARS and MORI engines."""

from __future__ import annotations

import hashlib
import json
import time

from beliefkv.experiments.causal_replay_sampling import PARAMETER_KEY, _device_indices
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain
from beliefkv.runtime.replay_token_capture import _wire


MARS_OUTPUT_KEY = "beliefkv_replay_output_ids"
MORI_RECEIPT_KEY = "beliefkv_baseline_native_output"


def token_sha256(ids):
    return hashlib.sha256(_wire(ids)).hexdigest()


def replace_mars_samples(runner, sampled, *, speculative):
    rows, targets = [], []
    for row, request_id in enumerate(runner.input_batch.req_ids):
        req = runner.requests[request_id]
        params = req.sampling_params
        encoded = (params.extra_args or {}).get(MARS_OUTPUT_KEY) if params else None
        if encoded is None:
            continue
        if speculative or runner.use_async_scheduling or sampled.ndim != 2 or sampled.shape[1] != 1:
            raise ValueError("MARS replay requires synchronous, non-speculative sampling")
        targets_for_request = getattr(req, "_beliefkv_replay_output_ids", None)
        if targets_for_request is None:
            targets_for_request = req._beliefkv_replay_output_ids = tuple(json.loads(encoded))
        seq_len = runner.seq_lens.np[row]
        # The native runner discards samples from intermediate prefill chunks.
        if seq_len < req.num_tokens:
            continue
        position = len(req.output_token_ids)
        if (
            seq_len != req.num_tokens or position >= len(targets_for_request)
            or position and req.output_token_ids[-1] != targets_for_request[position - 1]
        ):
            raise ValueError("MARS sample does not agree with native replay progress")
        rows.append(row)
        targets.append(targets_for_request[position])
    if rows:
        values = _device_indices(targets, sampled.device).to(sampled.dtype)
        if len(rows) == sampled.shape[0]:
            sampled[:, 0].copy_(values)
        else:
            sampled[:, 0].index_copy_(0, _device_indices(rows, sampled.device), values)


def mori_output_evidence(req, cache, output_delta):
    params = req.sampling_params.custom_params
    if type(params) is not dict or PARAMETER_KEY not in params:
        return None
    evidence = {
        "deltas": [(time.monotonic() * 1000., output_delta)],
        "clock_domain": local_monotonic_clock_domain(),
    }
    if req.finished():
        evidence.update(
            prompt_sha256=token_sha256(req.origin_input_ids), program_id=req.program_id,
            sampler_preserved=getattr(req, "_beliefkv_native_sampler_preserved", False),
            write_back_max_nodes=getattr(cache, "beliefkv_replay_write_back_cap", 0),
        )
    return evidence
