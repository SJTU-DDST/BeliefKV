import json
import os
from types import SimpleNamespace as NS
import time
from unittest.mock import patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_sglang_v0520_runtime import event, final_stage_runtime, req


@pytest.mark.parametrize("closed_prompt", (False, True))
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
    reason, outputs, matched, expected, closed_prompt,
):
    runtime = final_stage_runtime()
    runtime._clear_final_stage("join")
    runtime._child_report_notices.clear()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    child.beliefkv_metadata["parent_invocation_id"] = "parent"
    child.origin_input_ids = [98, 1, 99, 1] if closed_prompt else [1, 2]
    child.output_ids = [token for token in outputs if token != 99] if closed_prompt else outputs
    child.tokenizer = NS(decode=lambda ids, **_: "Report." if 10 in ids else "")
    child.finished_reason = NS(to_json=lambda: {"type": reason, "matched": matched})
    finished = [False]
    child.finished = lambda: finished[0]
    runtime._reasoning_close_token_ids = frozenset({99})
    runtime._reasoning_open_token_ids = frozenset({98})
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


@pytest.mark.parametrize("prompt,outputs,expected", (
    ([98, 1, 99, 1], [10, 11], True),
    ([98, 1, 99, 1], [], False),
    ([98, 1], [10, 11], False),
    ([98, 1], [10, 99, 11], True),
    ([98, 1, 99, 1], [98, 10, 11], False),
    ([98, 1, 99, 1], [10, 99, 98, 11], False),
    ([98, 1, 99, *([1] * 33)], [10, 11], False),
))
def test_visible_eos_body_respects_prompt_and_output_reasoning_state(
    prompt, outputs, expected,
):
    from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey

    runtime = NativeAdmissionRuntime()
    runtime._reasoning_open_token_ids = frozenset({98})
    runtime._reasoning_close_token_ids = frozenset({99})
    key = PrefillCandidateKey("child", "wf", "child", "ctx-child", 0, 0)
    request = NS(
        origin_input_ids=prompt, output_ids=outputs,
        finished_reason=NS(to_json=lambda: {"type": "stop"}),
        beliefkv_metadata={},
        tokenizer=NS(decode=lambda ids, **_: "Report." if 11 in ids else ""),
    )
    try:
        runtime._record_native_final_body(request, key)
        assert runtime._semantic_eos_proofs["child"] is expected
    finally:
        runtime.close()


@pytest.mark.parametrize("template,prompt,outputs,reason,expected", (
    ("plain assistant", [1, 2], [10], "stop", True),
    (None, [1, 2], [10], "stop", False),
    ("", [1, 2], [10], "stop", False),
    ({"default": "plain assistant"}, [1, 2], [10], "stop", False),
    ("assistant <think>", [1, 2], [10], "stop", False),
    ("assistant </think>", [1, 2], [10], "stop", False),
    ("plain assistant", [98, 1], [10], "stop", False),
    ("plain assistant", [98, 1, 99, 1], [10], "stop", True),
    ("plain assistant", [1, 2], [98, 10], "stop", False),
    ("plain assistant", [1, 2], [98, 1, 99, 10], "stop", True),
    ("plain assistant", [1, 2], [98, 99], "stop", False),
    ("plain assistant", [1, 2], [], "stop", False),
    ("plain assistant", [1, 2], [10], "length", False),
    ("plain assistant", [1, 2], [10], "abort", False),
))
def test_plain_template_eos_preserves_reasoning_and_finish_guards(
    tmp_path, template, prompt, outputs, reason, expected,
):
    from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey

    (tmp_path / "tokenizer_config.json").write_text(json.dumps({
        "chat_template": template,
        "added_tokens_decoder": {
            "98": {"content": "<think>"},
            "99": {"content": "</think>"},
        },
    }))
    runtime = NativeAdmissionRuntime(model_path=str(tmp_path))
    key = PrefillCandidateKey("child", "wf", "child", "ctx-child", 0, 0)
    request = NS(
        origin_input_ids=prompt, output_ids=outputs,
        finished_reason=NS(to_json=lambda: {"type": reason}),
        beliefkv_metadata={},
        tokenizer=NS(decode=lambda ids, **_: "Report." if 10 in ids else ""),
    )
    try:
        runtime._record_native_final_body(request, key)
        assert runtime._semantic_eos_proofs["child"] is expected
    finally:
        runtime.close()


@pytest.mark.parametrize("kind,expected", (
    ("report", True), ("tool", False), ("empty", False), ("length", False),
))
def test_real_plain_model_eos_reaches_join_stage_without_semantic_worker(kind, expected):
    model = os.environ.get("BELIEFKV_CONTEXT_TOKENIZER_TEST")
    if not model:
        pytest.skip("requires explicit local serving tokenizer")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
    runtime = final_stage_runtime(model_path=model)
    runtime._clear_final_stage("join")
    runtime._child_report_notices.clear()
    child = req("child")
    child.session_id, child.session_generation = "cs", 1
    child.beliefkv_metadata["context_epoch"] = 1
    child.beliefkv_metadata["parent_invocation_id"] = "parent"
    child.origin_input_ids = tokenizer.apply_chat_template([
        {"role": "system", "content": "You are a repository specialist."},
        {"role": "user", "content": "Return the completed findings to your parent."},
    ], tokenize=True, add_generation_prompt=True, enable_thinking=False)
    text = {
        "report": "The requested checks are complete.",
        "tool": (
            "<tool_call>\n<function=read_file>\n"
            "<parameter=file_path>README.md</parameter>\n</function>\n</tool_call>"
        ),
        "empty": "", "length": "The requested checks are",
    }[kind]
    child.output_ids = tokenizer.encode(text, add_special_tokens=False)
    child.output_ids.append(tokenizer.eos_token_id)
    child.tokenizer = tokenizer
    child.finished_reason = NS(to_json=lambda: {
        "type": "length" if kind == "length" else "stop",
        "matched": tokenizer.eos_token_id,
    })
    child.finished = lambda: True
    runtime.on_events((event(
        7, RuntimeEventKind.LLM_SUBMIT, invocation_id="child",
        context_id="ctx-child", context_epoch=1, attributes={"request_id": "child"},
    ),))
    runtime.register_visible_request(child)
    try:
        assert runtime._semantic_worker is None
        runtime.on_batch_completed(NS(reqs=(child,)))
        assert runtime._semantic_eos_proofs["child"] is expected
        assert bool(runtime._eos_prefetch_keys) is expected
        assert bool(runtime._final_stages) is expected
        assert not runtime._semantic_frames
        assert not runtime._semantic_forecasts
    finally:
        runtime.close()
