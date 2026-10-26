from array import array

import pytest

from beliefkv.runtime.agentix_capacity import AgentixNativeCapacity
from tests.test_agentix_native_preemption import native
from tests.test_agentix_swap import ack, running
from tests.test_full_only_native_lifecycle import full_native


def waiting(identity, tokens):
    from sglang.srt.managers.schedule_batch import Req
    from sglang.srt.sampling.sampling_params import SamplingParams

    return Req(
        rid=identity, origin_input_text="", origin_input_ids=array("q", tokens),
        sampling_params=SamplingParams(temperature=1., top_k=1, max_new_tokens=128),
        beliefkv_metadata={
            "root_workflow_id": "workflow", "invocation_id": identity,
            "context_id": identity, "context_epoch": 0,
        },
    )


def capacity(state, *, growth=2):
    return AgentixNativeCapacity(
        state.actor.transfers, context_limit=64, decode_growth_tokens=growth,
    )


def checkpoint(cache, req):
    req._refresh_fill_ids()
    req.set_extend_range(0, req.effective_kv_committed_len())
    cache.cache_unfinished_req(req, chunked=True)


def test_native_shared_prefix_counts_once_and_preserves_separate_decode_growth(native):
    state = native
    first, second = running(state.cache, "first"), running(state.cache, "second", output=(90, 91))
    checkpoint(state.cache, first)
    checkpoint(state.cache, second)
    ledger = capacity(state)
    ledger.prepare([first, second])
    assert ledger.required_tokens([first]) == ledger.required_tokens([second]) == 8
    assert ledger.required_tokens([first, second]) == 12
    assert ledger.resident(first) and ledger.resident(second)
    assert ledger.can_fit(second, (first,))
    assert not ledger.status()["native_physical_admission_authorized"]


def test_waiting_requests_are_bounded_by_the_actual_native_pool_capacity(native):
    state = native
    total = state.allocator.size_full
    first = waiting("first", range(1, total // 2 + 2))
    second = waiting("second", range(1000, 1000 + total // 2 + 1))
    ledger = capacity(state)
    ledger.prepare([first, second])
    assert ledger.total_tokens == total
    assert ledger.can_fit(first, ())
    assert not ledger.can_fit(second, (first,))
    assert not ledger.resident(first) and not ledger.resident(second)
    assert not state.writes and not state.loads


def test_recorded_sampler_output_limit_never_changes_observed_batch_footprint(native):
    state = native
    req = waiting("a", (1, 2, 3, 4))
    ledger = capacity(state)
    req.sampling_params.max_new_tokens = 1
    ledger.prepare([req])
    first = ledger.required_tokens([req])
    req.sampling_params.max_new_tokens = 1_000_000
    ledger.prepare([req])
    assert ledger.required_tokens([req]) == first == 6
    assert not ledger.status()["recorded_final_output_length_used"]


def test_host_only_checkpoint_is_budgeted_but_not_treated_as_resident(native):
    state = native
    req = running(state.cache)
    ledger = capacity(state)
    state.actor.prepare([req], running_batch=type("Batch", (), {"reqs": [req]})())
    ack(state.cache)
    state.actor.commit()
    ledger.prepare([req])
    assert ledger.required_tokens([req]) == 8
    assert not ledger.resident(req)
    assert ledger.can_fit(req, ())


def test_all_matches_finish_before_capacity_paths_are_resolved(native):
    state = native
    long = running(state.cache, "long", prompt=(1, 2, 3, 4, 5, 6))
    checkpoint(state.cache, long)
    short = waiting("short", (1, 2, 90, 91))
    ledger = capacity(state)
    ledger.prepare([long, short])
    assert ledger.required_tokens([long, short]) == 14
    assert ledger.resident(long) and not ledger.resident(short)


@pytest.mark.parametrize("changed", ("input", "output"))
def test_stale_or_duplicate_probes_cannot_authorize_a_changed_native_call(native, changed):
    state = native
    req = waiting("a", (1, 2, 3))
    ledger = capacity(state)
    ledger.prepare([req])
    with pytest.raises(ValueError, match="twice"):
        ledger.required_tokens([req, req])
    (req.origin_input_ids if changed == "input" else req.output_ids).append(99)
    with pytest.raises(ValueError, match="snapshot"):
        ledger.can_fit(req, ())
