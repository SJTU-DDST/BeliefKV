"""Execute the native scheduler loop, rather than invoking the guard by hand."""

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import pytest

from beliefkv.policy.tokencake import CapacityPlan
from beliefkv.experiments.tokencake_engine import (
    BATCH, COMMON_TREE, SCHEDULER, project_batch, project_scheduler, verify_native_hooks,
)
from tests.test_demand_prefill_pipeline_scheduler import (
    BatchConstructionReached, native_scheduler,
)
from tests.test_full_only_native_lifecycle import full_native
from tests.test_kvflow_runtime import selection
from tests.test_tokencake_native_sessions import submit_session
from tests.test_tokencake_paper_runtime import live


def scheduler_for(live, requests):
    runtime, _, cache, allocator, _ = live
    running = NS(reqs=[], batch_is_full=False, is_empty=lambda: True)
    callback = runtime.on_prefill_candidate_result
    scheduler, constructed, invoke = native_scheduler(NS(
        runtime=runtime, cache=cache, allocator=allocator, pipeline=None,
        running=running, waiting=list(requests), members=list(requests),
    ), enabled=False)
    scheduler.chunked_prefill_size = scheduler.max_prefill_tokens = 64
    runtime.on_prefill_candidate_result = callback
    selection(runtime, requests)
    return constructed, invoke


def test_actual_scheduler_calls_capacity_guard_before_native_admission(live):
    runtime, adapter, *_ = live
    req = submit_session(live, "scheduler-session", list(range(8)))
    constructed, invoke = scheduler_for(live, [req])
    with pytest.raises(BatchConstructionReached):
        invoke()
    assert constructed == [(req,)]
    assert adapter.capacity.requests[req.rid].pending_private == 8
    assert adapter.counts["admitted"] == 1
    assert adapter.capacity.counts["admission_probes"] > 0
    assert adapter.capacity.window.growth[req.rid] == 8


def test_actual_scheduler_capacity_refusal_never_initializes_or_admits_request(live):
    runtime, adapter, cache, allocator, _ = live
    req = submit_session(live, "refused-session", list(range(8)))
    constructed, invoke = scheduler_for(live, [req])
    with adapter._authority():
        adapter.capacity.set_plan(CapacityPlan(
            critical={"other"}, scores={"other": 1000.},
            reserved={"other": 64}, shared=0,
        ))
        adapter.capacity.set_waiting({"other": 10000})
    before = allocator.available_size()
    assert invoke()[0] is None
    assert not constructed and not adapter.capacity.requests
    assert adapter.counts["capacity_candidates_deferred"] == 1
    assert req.last_node is None and len(req.prefix_indices) == 0
    assert allocator.available_size() == before and not cache.ongoing_load_back


def test_actual_scheduler_settles_each_member_before_checking_the_next(live):
    runtime, adapter, *_ = live
    requests = [
        submit_session(live, "scheduler-a", list(range(20))),
        submit_session(
            live, "scheduler-b", list(range(100, 120)), context="peer", session="peer-native",
        ),
        submit_session(
            live, "scheduler-c", list(range(200, 220)), context="other", session="other-native",
        ),
    ]
    constructed, invoke = scheduler_for(live, requests)
    with pytest.raises(BatchConstructionReached):
        invoke()
    assert constructed == [tuple(requests[:2])]
    assert set(adapter.capacity.requests) == {req.rid for req in requests[:2]}
    assert adapter.counts["capacity_candidates_deferred"] == 1
    assert adapter.capacity.window.uncommitted == 8


def test_gpu_qualifier_fixture_itself_runs_the_actual_native_candidate_loop(live):
    from scripts.qualify_tokencake_runtime import NativeSchedulerAdmission

    runtime, adapter, cache, *_ = live
    req = submit_session(live, "qualifier-native-session", list(range(8)))
    selection(runtime, [req])
    fixture = NativeSchedulerAdmission(runtime, cache)
    assert fixture.admit((req,)) == (req,)
    assert fixture.executions == 1
    assert adapter.capacity.requests[req.rid].pending_private == 8


@pytest.mark.parametrize("selection", (None, 0))
def test_native_decode_retraction_settles_policy_before_releasing_the_selected_request(selection):
    from sglang.srt.managers.schedule_batch import ScheduleBatch

    requests = [
        NS(rid=str(index), beam_group=None, output_ids=[1], sampling_params=NS(max_new_tokens=8))
        for index in range(2)
    ]
    calls = []
    native = NS(
        reqs=requests, _get_decode_retraction_order=lambda reqs: [0, 1],
        check_decode_mem=lambda **kwargs: True,
        release_req=lambda index, *args: calls.append(("native_release", requests[index].rid)) or True,
        filter_batch=lambda **kwargs: None,
    )
    selected = 1 if selection is None else selection
    result = ScheduleBatch.retract_decode(
        native, select_victim=lambda *args: selection,
        before_release=lambda req, **kwargs: calls.append(("policy_release", req.rid)),
    )
    assert result[0] == [requests[selected]]
    assert calls == [("policy_release", str(selected)), ("native_release", str(selected))]


@pytest.mark.parametrize("selection", (True, -1, 2))
def test_native_decode_retraction_rejects_invalid_policy_victims_before_any_release(selection):
    from sglang.srt.managers.schedule_batch import ScheduleBatch

    calls = []
    native = NS(
        reqs=[NS(beam_group=None), NS(beam_group=None)],
        _get_decode_retraction_order=lambda reqs: [0, 1],
        release_req=lambda *args: calls.append(args),
    )
    with pytest.raises(ValueError, match="outside the native running batch"):
        ScheduleBatch.retract_decode(native, select_victim=lambda *args: selection)
    assert not calls


@pytest.mark.parametrize("fault", (None, "missing_guard", "wrong_release", "new_default", "unrelated"))
def test_engine_projection_accepts_only_the_optional_reviewed_bindings(fault):
    engine = Path(os.environ["SGLANG_SOURCE_CHECKOUT"])
    if fault is None:
        assert verify_native_hooks(engine)["exact_optional_hooks_projection_verified"] is True
        return
    relative = SCHEDULER if fault in ("missing_guard", "unrelated") else BATCH
    original = subprocess.check_output(
        ["git", "show", COMMON_TREE + ":" + relative], cwd=engine, text=True,
    )
    candidate = (engine / relative).read_text()
    if fault == "missing_guard":
        candidate = candidate.replace('"allow_native_prefill_candidate"', '"other_callback"')
    elif fault == "wrong_release":
        candidate = candidate.replace('reason="decode_retraction"', 'reason="other"')
    elif fault == "new_default":
        candidate = candidate.replace("select_victim=None", "select_victim=True")
    else:
        candidate += "\nunrelated_native_change = True\n"
    with pytest.raises(ValueError, match="TokenCake|outside"):
        (project_scheduler if relative == SCHEDULER else project_batch)(original, candidate)
