"""Read-only probes use the actual native FULL tree and PrefillAdder budget."""

from array import array
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.runtime.short_continuation_probe import inspect_short_continuations
from tests.test_full_only_native_lifecycle import full_native
from tests.test_reference_thunder_admission import full_runtime
from tests.test_sglang_v0520_runtime import event


@pytest.fixture
def case(full_native, monkeypatch, tmp_path):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.managers.schedule_policy import PrefillAdder
    from sglang.srt.sampling.sampling_params import SamplingParams
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, commits = full_native
    runtime = full_runtime(monkeypatch, tmp_path, capacity=10000, tool_prefetch=False)
    runtime._native_cache = cache
    requests = []
    for name, tokens, prefix in (
        ("long", list(range(1, 257)), 16),
        ("short", list(range(1001, 1011)), 8),
    ):
        node, generation = insert_session(cache, tokens[:prefix], name)
        runtime.on_events((
            event(0, Kind.WORKFLOW_START),
            event(1, Kind.INVOCATION_CREATE, invocation_id=name, context_id=f"ctx-{name}"),
        ) if name == "long" else (
            event(2, Kind.INVOCATION_CREATE, invocation_id=name, context_id=f"ctx-{name}"),
        ))
        req = Req(
            rid=name, origin_input_text="", origin_input_ids=array("q", tokens),
            sampling_params=SamplingParams(max_new_tokens=2), session_id=name,
            beliefkv_metadata={
                "root_workflow_id": "wf", "invocation_id": name,
                "context_id": f"ctx-{name}", "context_epoch": 0,
            },
        )
        req.session_generation = generation
        assert runtime.register_visible_request(req)
        assert runtime._agent_admission.allows(runtime.visible[name])
        requests.append((req, node))
    owner, short = (item[0] for item in requests)
    owner.init_next_round_input(cache)
    running = NS(reqs=[])
    adder = PrefillAdder(
        page_size=1, tree_cache=cache, token_to_kv_pool_allocator=allocator,
        running_batch=running, new_token_ratio=1., rem_input_tokens=32,
        rem_chunk_tokens=32, max_running_requests=24,
    )
    cache._execute_and_commit_kv_backup = Mock(wraps=cache._execute_and_commit_kv_backup)
    f = NS(
        runtime=runtime, cache=cache, allocator=allocator, writes=writes, loads=loads,
        commits=commits, owner=owner, short=short, leaf=requests[1][1],
        running=running, adder=adder, queue=[short],
    )
    try:
        yield f
    finally:
        runtime.close()


def probe(f, **kwargs):
    return inspect_short_continuations(
        f.runtime, f.cache, f.queue, running_batch=f.running,
        adder=f.adder, chunked_req=f.owner, soft_budget_ms=1., **kwargs,
    )


def snapshot(f):
    return {
        "free": f.allocator.available_size(),
        "rows": f.cache.req_to_token_pool.available_size(),
        "short_prefix": tuple(f.short.prefix_indices.tolist()),
        "owner_prefix": tuple(f.owner.prefix_indices.tolist()),
        "short_range": f.short.extend_range, "owner_range": f.owner.extend_range,
        "short_fill": tuple(f.short.full_untruncated_fill_ids),
        "owner_fill": tuple(f.owner.full_untruncated_fill_ids),
        "lock": f.leaf.component_data[0].lock_ref,
        "host_lock": f.leaf.component_data[0].host_lock_ref,
        "anchors": f.cache.session_refs.snapshot_latest_session_leaf_anchors(
            "short", f.short.session_generation,
        ),
        "adder_offset": (f.adder.cur_rem_token_offset, f.adder.rem_total_token_offset),
        "adder_compute": (f.adder.rem_chunk_tokens, f.adder.rem_input_tokens),
        "can_run": tuple(f.adder.can_run_list),
        "programs": deepcopy(f.runtime._agent_admission.policy.programs),
        "queue": tuple(f.queue),
    }


def test_actual_native_budget_approves_two_plus_thirty_without_claiming_capacity(case):
    before = snapshot(case)
    result = probe(case)
    assert len(result["candidates"]) == 1
    member = result["candidates"][0]
    assert member["checkpoint_tokens"] == 8
    assert member["short_input_tokens"] == 2 and member["owner_input_tokens"] == 30
    assert member["combined_input_tokens"] == 32
    assert member["owner_remains_chunked"]
    assert member["native_eviction_credit_tokens"] == 0
    assert not result["physical_capacity_credit"] and not result["priority_or_admission_authority"]
    assert snapshot(case) == before
    assert not case.writes and not case.loads and not case.commits
    case.cache._execute_and_commit_kv_backup.assert_not_called()


def test_actual_native_adder_and_allocator_reproduce_the_observed_joint_shape(case):
    f = case
    predicted = probe(f)["candidates"][0]
    f.short.init_next_round_input(f.cache)
    f.adder.add_one_req(f.short, has_chunked_req=True, truncation_align_size=None)
    assert f.adder.can_run_list == [f.short]
    assert f.adder.add_chunked_req(f.owner) is f.owner
    assert f.adder.can_run_list == [f.short, f.owner]
    assert [req.extend_range.length for req in f.adder.can_run_list] == [
        predicted["short_input_tokens"], predicted["owner_input_tokens"],
    ]
    rows = f.cache.req_to_token_pool.alloc_rows(2)
    allocation = f.allocator.alloc(predicted["combined_input_tokens"] + 2 * f.adder.page_size)
    assert rows is not None and allocation is not None
    f.allocator.free(allocation)
    f.cache.req_to_token_pool.free_rows(rows)
    f.cache.dec_lock_ref(f.short.last_node, f.short.lock_receipt)
    f.short.lock_receipt = None
    assert not f.writes and not f.loads


@pytest.mark.parametrize("change", (
    "session", "epoch", "inactive", "blocked", "finished", "already_started",
    "multimodal", "large_increment", "host_only", "pending_load", "no_rows",
    "running_limit", "one_prefill", "low_physical_free", "short_chunk",
))
def test_unknown_cold_or_unavailable_shapes_never_become_an_admission(case, change):
    f = case
    if change == "session":
        f.short.session_generation += 1
    elif change == "epoch":
        f.short.beliefkv_metadata["context_epoch"] += 1
    elif change == "inactive":
        f.runtime._agent_admission.policy.programs["short"].active = False
    elif change == "blocked":
        f.runtime.on_events((event(
            3, Kind.TOOL_START, invocation_id="short", attributes={"tool_run_id": "tool"},
        ),))
    elif change == "finished":
        f.short.to_finish = object()
    elif change == "already_started":
        f.short.output_ids.append(1)
    elif change == "multimodal":
        f.short.multimodal_inputs = object()
    elif change == "large_increment":
        f.short.origin_input_ids.extend([1100] * 10)
    elif change == "host_only":
        f.leaf.component_data[0].host_value = f.leaf.component_data[0].value
        f.leaf.component_data[0].value = None
    elif change == "pending_load":
        f.leaf.load_back_pending_id = 123
    elif change == "no_rows":
        f.cache.req_to_token_pool.free_slots.clear()
    elif change == "running_limit":
        f.adder.max_running_requests = 1
    elif change == "one_prefill":
        f.adder.prefill_max_requests = 1
    elif change == "low_physical_free":
        assert f.allocator.alloc(f.allocator.available_size() - 4) is not None
        assert f.cache.evictable_size() > 4
    else:
        f.adder.rem_chunk_tokens = 8
    before = snapshot(f)
    assert not probe(f)["candidates"]
    assert snapshot(f) == before
    assert not f.loads and not f.writes


def test_running_decode_growth_is_still_charged_to_the_joint_native_budget(case):
    f = case
    f.adder.rem_total_token_offset += 16
    before = snapshot(f)
    result = probe(f)
    assert not result["candidates"]
    assert result["reasons"]["native_joint_budget_refused"]
    assert snapshot(f) == before


def test_source_with_outbound_backup_is_readable_without_waiting_for_that_backup(case):
    case.leaf.write_through_pending_id = 123
    before = snapshot(case)
    assert len(probe(case)["candidates"]) == 1
    assert snapshot(case) == before


def test_short_request_reserves_native_generation_budget_independent_of_final_output(case):
    case.short.sampling_params.max_new_tokens = 32
    before = snapshot(case)
    assert not probe(case)["candidates"]
    assert snapshot(case) == before


def test_unknown_native_checkpoint_and_budget_are_diagnostics_not_scheduler_actions(case, monkeypatch):
    monkeypatch.setattr(
        case.cache, "inspect_beliefkv_reentry", Mock(side_effect=RuntimeError("unavailable")),
    )
    before = snapshot(case)
    result = probe(case)
    assert not result["candidates"] and result["reasons"]["checkpoint_inspection_failed"] == 1
    assert snapshot(case) == before


def test_request_limit_counts_the_chunk_owner_and_extra_member(case):
    f = case
    f.running.reqs = [NS() for _ in range(23)]
    assert probe(f)["reasons"]["request_slot_limit"] == 1
    assert not f.adder.can_run_list


def test_joint_shape_also_fits_the_real_request_row_context_limit(case):
    case.cache.req_to_token_pool.max_context_len = 9
    before = snapshot(case)
    result = probe(case)
    assert not result["candidates"] and result["reasons"]["request_context_limit"] == 1
    assert snapshot(case) == before


def test_a_different_native_cache_cannot_authorize_a_runtime_observation(case):
    case.runtime._native_cache = NS()
    result = probe(case)
    assert not result["candidates"] and result["reasons"]["unsupported_scope"] == 1


@pytest.mark.parametrize("checks", [0, 9, True])
def test_probe_does_not_accept_an_unbounded_queue_scan(case, checks):
    with pytest.raises(ValueError, match="bounded"):
        probe(case, maximum_checks=checks)
