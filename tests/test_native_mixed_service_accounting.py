from collections import Counter
import time
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from scripts.audit_decode_batch_efficiency import (
    OutputConservation, accumulate, decode_view, summarize,
)
from tests.test_v0520_native_telemetry import _Mode, _read


def test_mixed_extend_counts_decode_and_first_tokens_once_without_counting_input_as_output(tmp_path):
    telemetry = NativeReactiveTelemetry(tmp_path / "server")

    def request(identity, length):
        return NS(
            rid=identity, cache_request_handle=NS(attempt_id=0),
            session_id=identity, session_generation=0,
            beliefkv_metadata={
                "root_workflow_id": "w", "invocation_id": identity,
                "context_id": identity, "context_epoch": 0,
            },
            origin_input_ids=list(range(length)), output_ids=[],
            cached_tokens_device=0, cached_tokens_host=0, mamba_host_hit_length=0,
            extend_input_len=length, sampling_params=NS(max_new_tokens=8),
            finished=lambda: False,
        )

    resident, cold = request("resident", 100), request("cold", 4000)
    for req in (resident, cold):
        telemetry.on_enqueue(req)

    def forward(index, phase, reqs, deltas, *, decoding=(), prefixes=None, lengths=None):
        batch = NS(
            forward_mode=_Mode(phase), launch_ts=time.monotonic(),
            forward_iter=index, reqs=reqs, decoding_reqs=decoding,
            prefix_lens=prefixes, extend_lens=lengths,
        )
        telemetry.on_launch(batch)
        for req, delta in zip(reqs, deltas):
            req.output_ids.extend([7] * delta)
        telemetry.on_completed(batch, NS(can_run_cuda_graph=phase == "decode"))

    forward(1, "prefill", [resident], [1])
    forward(2, "prefill", [cold, resident], [0, 1], decoding=[resident],
            prefixes=[0, 100], lengths=[2048, 1])
    forward(3, "prefill", [cold, resident], [1, 1], decoding=[resident],
            prefixes=[2048, 101], lengths=[1952, 1])
    forward(4, "decode", [cold, resident], [1, 0])
    telemetry.close()
    rows = [
        row for row in _read(tmp_path / "server/runtime_audit.jsonl")
        if row.get("event") == "gpu_service_sample"
    ]
    assert [row["phase"] for row in rows] == ["prefill", "prefill", "prefill", "decode"]
    assert [row["decode_batch_size"] for row in rows] == [0, 1, 1, 2]
    assert rows[1]["request_samples"][0]["token_delta"] == 2048
    assert rows[1]["request_samples"][0]["output_token_delta"] == 0
    assert rows[1]["request_samples"][1]["phase"] == "decode"
    assert rows[1]["request_samples"][1]["token_delta"] == 1
    assert rows[2]["request_samples"][0]["token_delta"] == 1952
    assert rows[2]["request_samples"][0]["output_token_delta"] == 1
    output, counts = OutputConservation(), Counter()
    for row in rows:
        output.observe(row)
        decoded = decode_view(row, mixed_enabled=True)
        if decoded is not None:
            accumulate(counts, decoded, [1, 2, 4])
    result = output.verify({"resident": {"output_tokens": 3}, "cold": {"output_tokens": 2}})
    assert result["total_output_tokens"] == 5
    assert result["output_tokens_by_service_role"] == {"prefill": 2, "decode": 3}
    assert summarize(counts)["output_tokens"] == 3
    assert summarize(counts)["worker_ms"] == pytest.approx(
        sum(row["service_elapsed_ms"] for row in rows[1:]),
    )
    assert summarize(counts)["graph_batches"] == 1


def test_mixed_old_metrics_are_refused_instead_of_silently_losing_decode():
    with pytest.raises(ValueError, match="all-mode"):
        decode_view({"phase": "prefill", "request_samples": []}, mixed_enabled=True)


@pytest.mark.parametrize("cause", ("missing", "duplicate", "gap", "input_as_output", "receipt"))
def test_output_conservation_refuses_incomplete_or_double_counted_evidence(cause):
    row = {
        "output_token_accounting": "observed_output_ids_delta_all_forward_modes_v1",
        "request_samples": [{
            "request_id": "a", "phase": "decode",
            "output_tokens_reported_before": 0, "output_tokens_after": 1, "output_token_delta": 1,
        }],
    }
    output = OutputConservation()
    if cause == "missing":
        del row["request_samples"][0]["output_token_delta"]
    elif cause == "gap":
        row["request_samples"][0]["output_tokens_reported_before"] = 1
    elif cause == "input_as_output":
        row["request_samples"][0]["output_token_delta"] = 2048
    with pytest.raises(ValueError):
        output.observe(row)
        if cause == "duplicate":
            output.observe(row)
        output.verify({"a": {"output_tokens": 2}})
