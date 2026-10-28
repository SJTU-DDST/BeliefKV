from collections import Counter
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.runtime.demand_prefill_pipeline import create_pipeline
from beliefkv.runtime.sglang_v0520_admission import PrefillCandidateKey


def fixture():
    key = PrefillCandidateKey("request-b", "root", "agent-b", "context-b", 2, 0, "session-b", 0)
    receipt, finish = object(), NS(ready=False, queries=0)

    def query():
        finish.queries += 1
        return finish.ready

    finish.query = query
    request = NS(
        rid=key.request_id, session_id=key.session_id, session_generation=key.session_generation,
        beliefkv_metadata={
            "root_workflow_id": key.root_workflow_id, "invocation_id": key.invocation_id,
            "context_id": key.context_id, "context_epoch": key.context_epoch,
        },
        cache_request_handle=NS(attempt_id=key.attempt_id),
        lock_receipt=receipt, last_node=7, host_hit_length=8, host_loaded_length=8,
        kv=NS(req_pool_idx=None),
        beam_group=None, to_finish=None, finished=lambda: False,
    )
    controller = NS(
        write_policy="write_back", load_queue=[NS(beliefkv_command_id=None)],
        ack_load_queue=[], starts=0,
        native_tokens=8, native_nodes=[7],
    )
    cache = NS(
        tree_components=(0,), buffer_pipeline=None, linker=None, enable_storage=False,
        host_memory_mode="cache", cache_controller=controller, releases=[],
        enable_session_radix_cache=True,
        beliefkv_prefill_load_consumer=lambda _reqs, consumer: consumer,
        supports_beliefkv_overlap_prefetch=lambda: True,
    )

    def start():
        controller.starts += 1
        controller.load_queue.clear()
        controller.ack_load_queue.append(NS(
            finish_event=finish, num_tokens_by_pool={"kv": controller.native_tokens},
            submit_ts_ms=100., node_ids=controller.native_nodes,
            num_bytes=controller.native_tokens * 98304,
        ))
        return 1

    cache.ready_to_load_host_cache = start
    cache.dec_lock_ref = lambda node, token: cache.releases.append((node, token))
    admission = NS(eligible=True)
    admission.allows = lambda candidate: candidate == key and admission.eligible
    runtime = NS(
        demand_prefill_pipeline_enabled=True, internal_comparison_mode="full",
        _paper_baseline=None, _agent_admission=admission, visible={key.request_id: key},
        _terminal=lambda candidate: False, counts=Counter(), _opportunity_writer=None,
    )
    running = NS(reqs=[NS(finished=lambda: False, to_finish=None)], is_prefill_only=False)
    adder = NS(can_run_list=[request], new_chunked_req=None)
    pipeline = create_pipeline(runtime, cache)
    return NS(
        pipeline=pipeline, runtime=runtime, cache=cache, controller=controller, finish=finish,
        request=request, key=key, receipt=receipt, running=running, adder=adder,
        waiting=[request],
    )


def stage(f, **changes):
    return f.pipeline.stage(f.request, **{
        "adder": f.adder, "running_batch": f.running, "candidate_count": 1,
        "native_load_queue_was_empty": True, "chunked_req": None, **changes,
    })


def test_resident_decode_continues_with_no_second_restore_or_native_row():
    f = fixture()
    assert stage(f)
    for _ in range(5):
        assert f.pipeline.defer(f.waiting, f.running)
    assert f.controller.starts == 1
    assert f.request.kv.req_pool_idx is None
    assert f.adder.can_run_list == []
    assert f.waiting == [f.request]
    assert f.request.lock_receipt is f.receipt
    assert not f.cache.releases
    f.finish.ready = True
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.pipeline.pending is None
    assert f.request.lock_receipt is None
    assert f.cache.releases == [(7, f.receipt)]
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.cache.releases == [(7, f.receipt)]


@pytest.mark.parametrize("native_consumer,expected", [(-1, 1), (2, 2)])
def test_no_decode_fallback_reacquires_native_lock_and_keeps_layer_dependency(native_consumer, expected):
    f = fixture()
    assert stage(f)
    f.running.reqs = []
    assert not f.pipeline.defer(f.waiting, f.running)
    f.request.lock_receipt = object()
    batch = NS(reqs=[f.request], hicache_consumer_index=native_consumer)
    f.pipeline.bind(batch)
    assert batch.hicache_consumer_index == expected
    assert f.pipeline.pending is None
    assert f.cache.releases == [(7, f.receipt)]
    assert f.request.lock_receipt is not None


def test_existing_sibling_batch_preference_is_not_split():
    f = fixture()
    key = replace(f.key, request_id="sibling", invocation_id="sibling")
    sibling = NS(**{
        **vars(f.request), "rid": key.request_id, "last_node": 8, "lock_receipt": object(),
        "beliefkv_metadata": {**f.request.beliefkv_metadata, "invocation_id": key.invocation_id},
    })
    f.runtime.visible[key.request_id] = key
    f.runtime._agent_admission.allows = lambda candidate: candidate in (f.key, key)
    f.adder.can_run_list.append(sibling)
    f.waiting.append(sibling)
    f.controller.native_tokens = 16
    f.controller.native_nodes = [7, 8]
    receipt = sibling.lock_receipt
    assert stage(f, candidate_count=2)
    assert f.adder.can_run_list == []
    assert f.pipeline.defer(f.waiting, f.running)
    f.finish.ready = True
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.cache.releases == [(7, f.receipt), (8, receipt)]
    assert f.runtime.counts["demand_restore_staged_requests"] == 2
    assert f.runtime.counts["demand_restore_staged_native_batches"] == 1


@pytest.mark.parametrize("change", [
    {"native_load_queue_was_empty": False}, {"chunked_req": object()},
    {"candidate_count": 0},
])
def test_unsupported_native_selection_keeps_original_admission(change):
    f = fixture()
    assert not stage(f, **change)
    assert f.adder.can_run_list == [f.request]
    assert f.controller.starts == 0


def test_existing_native_row_and_chunked_target_are_never_requeued():
    f = fixture()
    f.request.kv.req_pool_idx = 0
    assert not stage(f)
    f.request.kv.req_pool_idx = None
    f.adder.new_chunked_req = f.request
    assert not stage(f)
    assert f.controller.starts == 0


def test_staging_accounts_actual_loaded_tail_instead_of_clamping_to_logical_host_hit():
    f = fixture()
    f.request.host_hit_length = 4
    assert stage(f)
    assert f.pipeline.pending.native_full_tokens == 8
    assert f.pipeline.pending.native_bytes == 8 * 98304


def test_native_abort_releases_only_owned_receipt_before_queue_cleanup():
    f = fixture()
    assert stage(f)
    f.pipeline.on_abort(NS(abort_all=False, rid="request"), exact=True)
    assert f.pipeline.pending is not None
    f.pipeline.on_abort(NS(abort_all=False, rid="request"), exact=False)
    assert f.pipeline.pending is None
    assert f.request.lock_receipt is None
    assert f.cache.releases == [(7, f.receipt)]
    f.pipeline.close()
    assert f.cache.releases == [(7, f.receipt)]


def test_eligibility_change_grants_no_permit_and_retains_only_transfer_identity():
    f = fixture()
    assert stage(f)
    f.runtime._agent_admission.eligible = False
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.pipeline.pending is not None
    assert f.cache.releases == [(7, f.receipt)]
    assert not f.runtime._agent_admission.eligible
    f.finish.ready = True
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.pipeline.pending is None
    assert f.cache.releases == [(7, f.receipt)]


def test_terminal_request_releases_receipt_without_waiting_for_dma():
    f = fixture()
    assert stage(f)
    f.runtime.visible.clear()
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.pipeline.pending is None
    assert not f.finish.ready
    assert f.cache.releases == [(7, f.receipt)]


@pytest.mark.parametrize("change", ("attempt", "epoch", "session", "finished", "abort"))
def test_current_native_identity_is_rechecked_before_deferring_decode(change):
    f = fixture()
    assert stage(f)
    if change == "attempt":
        f.request.cache_request_handle.attempt_id += 1
    elif change == "epoch":
        f.request.beliefkv_metadata["context_epoch"] += 1
    elif change == "session":
        f.request.session_generation += 1
    elif change == "finished":
        f.request.finished = lambda: True
    else:
        f.request.to_finish = object()
    assert not f.pipeline.defer(f.waiting, f.running)
    assert f.pipeline.pending is None
    assert f.cache.releases == [(7, f.receipt)]


def test_unannounced_native_lock_mutation_is_rejected():
    f = fixture()
    assert stage(f)
    f.request.lock_receipt = object()
    f.finish.ready = True
    with pytest.raises(RuntimeError, match="lock receipt changed"):
        f.pipeline.defer(f.waiting, f.running)
    assert not f.cache.releases


@pytest.mark.parametrize("attribute,value", [
    ("tree_components", (0, 2)), ("enable_storage", True),
    ("linker", object()), ("buffer_pipeline", object()),
    ("enable_session_radix_cache", False),
    ("beliefkv_prefill_load_consumer", None),
    ("supports_beliefkv_overlap_prefetch", lambda: False),
])
def test_factory_rejects_unsupported_cache_contract(attribute, value):
    f = fixture()
    setattr(f.cache, attribute, value)
    with pytest.raises(ValueError, match="FULL-only"):
        create_pipeline(f.runtime, f.cache)


def test_disabled_and_paper_paths_are_isolated():
    f = fixture()
    f.runtime.demand_prefill_pipeline_enabled = False
    assert create_pipeline(f.runtime, object()) is None
    f.runtime.demand_prefill_pipeline_enabled = True
    f.runtime._paper_baseline = object()
    with pytest.raises(ValueError, match="Full agent"):
        create_pipeline(f.runtime, f.cache)
