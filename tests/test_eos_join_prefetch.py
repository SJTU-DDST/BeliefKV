from types import SimpleNamespace as NS
import time
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, final_stage_runtime, req


@pytest.mark.parametrize("reason,outputs,matched,expected", (
    ("stop", [1, 99, 10], None, True),
    ("stop", [1, 99, 10], "NaN happened", False),
    ("length", [1, 99, 10], None, False),
    ("abort", [1, 99, 10], None, False),
    ("stop", [1, 99], None, False),
    ("stop", [1, 2], None, False),
    ("stop", [1, 99, 10, 77], None, False),
))
def test_finished_child_eos_without_worker_or_streaming(
    reason, outputs, matched, expected,
):
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime._child_report_notices.clear()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    child.beliefkv_metadata["parent_invocation_id"] = "parent"
    child.origin_input_ids, child.output_ids = [1, 2], outputs
    child.tokenizer = NS(decode=lambda ids, **_: "Report." if 10 in ids else "")
    child.finished_reason = NS(to_json=lambda: {"type": reason, "matched": matched})
    finished = [False]
    child.finished = lambda: finished[0]
    runtime._reasoning_close_token_ids = frozenset({99})
    runtime._tool_open_token_ids = frozenset({77})
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1, attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    try:
        for _ in range(3):
            runtime.on_batch_completed(NS(reqs=(child,)))
        assert runtime._semantic_worker is None
        assert not runtime._semantic_progress
        assert not runtime._semantic_frames
        assert not runtime._semantic_forecasts
        assert not runtime._decoded_scan_positions
        assert not runtime._eos_prefetch_keys
        finished[0] = True
        runtime.on_batch_completed(NS(reqs=(child,)))
        assert runtime._semantic_eos_proofs["child"] is expected
        assert bool(runtime._eos_prefetch_keys) is expected
        assert bool(runtime._final_stages) is expected
        assert "child" not in runtime.visible
        runtime._h2d_samples.extend(((100, 10.),) * 3)
        runtime._native_cache = NS(cache_controller=NS(mem_pool_host=NS(entry_map={
            "kv": NS(host_pool=NS(size_per_token=1)),
            "mamba": NS(host_pool=NS(size_per_token=1)),
        })))
        plan = NS(
            step="restore", fits_current_free_lists=True,
            required_full_tokens=100, required_mamba_slots=0,
        )
        with patch.object(runtime, "_wait_prefetch_opportunity", return_value=plan) as inspect:
            runtime._roll_final_stage()
        assert inspect.call_count == int(expected)
        assert (runtime._join_ticket is not None) is expected
        if expected:
            assert runtime._join_ticket_has_native_eos(runtime._join_ticket)
            assert runtime._join_ticket.work_endpoint_tokens is None
            runtime._semantic_finished["child"] = (
                time.monotonic() * 1000. - runtime.eos_prefetch_evidence_window_ms - 1.,
                len(outputs),
            )
            assert not runtime._join_ticket_has_native_eos(runtime._join_ticket)
    finally:
        runtime.close()


def test_eos_runtime_never_loads_legacy_semantic_model(monkeypatch):
    monkeypatch.setenv("BELIEFKV_SEMANTIC_REPORT_ARTIFACT", "/missing/legacy/model.json")
    runtime = NativeAdmissionRuntime()
    try:
        assert runtime._semantic_worker is None
        assert runtime.join_prefetch_trigger == "observed_eos"
    finally:
        runtime.close()
