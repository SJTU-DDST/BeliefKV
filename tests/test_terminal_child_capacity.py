from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from beliefkv.core.events import RuntimeEventKind
from beliefkv.runtime.native_transfer_policy import PrefetchResidencyBudget
from beliefkv.runtime.sglang_v0520_physical import PrefetchLoadStep
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime, _PrefetchServiceLease
from tests.test_sglang_v0520_runtime import event, req, select


def reclaimed(*, device=None, retry=()):
    return {
        "freed_device_units": device or {}, "freed_host_units": {},
        "discarded_nodes": ((11, 1),) if device else (),
        "retry_anchors": retry, "reason": None,
    }


@pytest.fixture
def family(monkeypatch):
    monkeypatch.setenv("BELIEFKV_ENABLE_RESIDENT_FIRST", "1")
    runtime = NativeAdmissionRuntime()
    requests = {}
    runtime.on_events((event(0, RuntimeEventKind.WORKFLOW_START),))
    for seq, name in enumerate(("parent", "child1", "child2", "ordinary"), 1):
        request = req(name)
        request.session_id, request.session_generation = f"s-{name}", 1
        runtime.register_visible_request(request)
        runtime.on_events((event(
            seq, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id=name, context_id=f"ctx-{name}",
            parent_invocation_id="parent" if name.startswith("child") else None,
        ),))
        requests[name] = request
    runtime.on_events((
        event(5, RuntimeEventKind.JOIN_CREATE, join_id="join",
              member_invocation_ids=("child1", "child2")),
        event(6, RuntimeEventKind.JOIN_WAIT, invocation_id="parent", join_id="join"),
    ))
    records = []
    runtime._opportunity_writer = NS(record=records.append)
    cache = NS(
        retire_beliefkv_child_session=Mock(side_effect=lambda **_: reclaimed(device={0: 8, 2: 1})),
        inspect_beliefkv_reentry=Mock(return_value={
            "missing_full_tokens": 0, "missing_mamba_slots": 0,
            "device_checkpoint_tokens": 4,
        }),
    )
    runtime.attach_native_cache(cache)
    yield runtime, requests, cache, records
    runtime._opportunity_writer = None
    runtime.close()


def returned(seq, child):
    return event(seq, RuntimeEventKind.RETURN,
                 invocation_id=child, context_id=f"ctx-{child}")


def parent_request(runtime, previous, *, epoch=1, generation=1):
    request = req("parent")
    request.rid = "parent-next"
    request.session_id, request.session_generation = previous.session_id, generation
    request.beliefkv_metadata["context_epoch"] = epoch
    runtime.on_events((event(
        10, RuntimeEventKind.LLM_SUBMIT, invocation_id="parent", context_id="ctx-parent",
        context_epoch=epoch, attributes={"request_id": request.rid},
    ),))
    runtime.register_visible_request(request)
    return request


def test_sibling_return_releases_capacity_without_admitting_waiting_parent(family):
    runtime, requests, cache, records = family
    runtime.on_events((returned(7, "child1"),))
    cache.retire_beliefkv_child_session.assert_called_once_with(
        session_id="s-child1", session_generation=1,
    )
    assert not runtime.graph.joins["join"].satisfied
    handoff = runtime._parent_capacity_handoffs["ctx-parent"]
    assert handoff.freed_device_units == {0: 8, 2: 1}
    assert runtime._live_parent_capacity_handoff(runtime.visible["parent"]) is None
    assert runtime._parent_handoff_priority_promoted is None
    assert records[0]["parent_context_id"] == "ctx-parent"
    assert records[0]["child_return_event_ts_ms"] == 7.
    assert records[0]["child_return_observed_wall_ts_ms"] <= records[0]["ts_ms"]
    assert "ctx-child1" not in runtime.context_sessions


def test_batched_tool_end_does_not_consume_return_session_before_reclaim(family):
    runtime, _, cache, _ = family
    runtime.on_events((event(
        7, RuntimeEventKind.TOOL_START, invocation_id="child1", context_id="ctx-child1",
    ),))
    runtime.on_events((
        event(8, RuntimeEventKind.TOOL_END, invocation_id="child1", context_id="ctx-child1"),
        returned(9, "child1"),
    ))
    cache.retire_beliefkv_child_session.assert_called_once()
    assert runtime._parent_capacity_handoffs["ctx-parent"].last_return_ts_ms > 9.


def test_last_child_final_notice_reuses_parent_plan_after_sibling_release(family):
    runtime, _, cache, _ = family
    runtime.on_events((returned(7, "child1"),))
    runtime.on_events((event(
        8, RuntimeEventKind.STRUCTURED_ACTION,
        invocation_id="child2", context_id="ctx-child2", context_epoch=0, join_id="join",
        attributes={
            "beliefkv_child_completion_intent": True,
            "child_completion_signal_kind": "stage",
            "estimated_final_report_tokens": 128,
        },
    ),))
    assert runtime._final_stages["join"].key == runtime.context_sessions["ctx-parent"]
    assert runtime._final_stages["join"].child_id == "child2"
    assert runtime._parent_capacity_handoffs["ctx-parent"].freed_device_units == {0: 8, 2: 1}
    cache.retire_beliefkv_child_session.assert_called_once()
    assert not runtime.graph.joins["join"].satisfied


def test_parent_next_request_preserves_sibling_release_and_consumes_handoff_once(family):
    runtime, requests, _, records = family
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    handoff = runtime._live_parent_capacity_handoff(runtime.visible[parent.rid])
    assert handoff.freed_device_units == {0: 16, 2: 2}
    assert handoff.child_ids == {"child1", "child2"}
    assert select(runtime, [requests["ordinary"], parent]).candidates[0] is parent
    assert runtime._parent_handoff_priority_promoted == parent.rid
    runtime.on_prefill_candidate_result(parent, admitted=True, result="ok")
    assert runtime.counts["parent_handoff_priority_admitted"] == 1
    assert runtime.counts["prefetch_priority_admitted"] == 0
    runtime.on_batch_completed(NS(reqs=[parent]))
    runtime.on_batch_completed(NS(reqs=[parent]))
    assert runtime.counts["parent_capacity_handoff_first_service"] == 1
    assert "ctx-parent" not in runtime._parent_capacity_handoffs
    service = [row for row in records if row["event"] == "parent_capacity_handoff_first_service"]
    assert len(service) == 1
    assert records[0]["ts_ms"] <= service[0]["last_observed_child_return_ts_ms"] <= service[0]["ts_ms"]
    assert service[0]["freed_device_units"] == {0: 16, 2: 2}
    assert runtime._prefetch_first_services == {}


def test_stale_batch_identity_cannot_consume_the_parent_handoff(family):
    runtime, requests, _, _ = family
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    stale = NS(**vars(parent))
    stale.session_generation = 2
    runtime.on_batch_completed(NS(reqs=[stale]))
    assert "ctx-parent" in runtime._parent_capacity_handoffs
    assert runtime.counts["parent_capacity_handoff_first_service"] == 0
    runtime.on_batch_completed(NS(reqs=[parent]))
    assert runtime.counts["parent_capacity_handoff_first_service"] == 1


def test_return_and_parent_submit_in_one_batch_keep_the_checkpoint_capacity_link(family):
    runtime, requests, _, _ = family
    runtime.on_events((
        returned(7, "child1"), returned(8, "child2"),
        event(9, RuntimeEventKind.LLM_SUBMIT, invocation_id="parent",
              context_id="ctx-parent", context_epoch=1,
              attributes={"request_id": "parent-next"}),
    ))
    parent = req("parent")
    parent.rid = "parent-next"
    parent.session_id, parent.session_generation = requests["parent"].session_id, 1
    parent.beliefkv_metadata["context_epoch"] = 1
    runtime.register_visible_request(parent)
    handoff = runtime._live_parent_capacity_handoff(runtime.visible[parent.rid])
    assert handoff.freed_device_units == {0: 16, 2: 2}
    runtime.on_batch_completed(NS(reqs=[parent]))
    assert runtime.counts["parent_capacity_handoff_first_service"] == 1


@pytest.mark.parametrize("missing", ("missing_full_tokens", "missing_mamba_slots"))
def test_host_only_or_missing_state_parent_is_not_promoted(family, missing):
    runtime, requests, cache, _ = family
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    cache.inspect_beliefkv_reentry.return_value[missing] = 1
    select(runtime, [requests["ordinary"], parent])
    assert runtime._parent_handoff_priority_promoted is None


@pytest.mark.parametrize("change", ("session", "generation", "epoch"))
def test_parent_identity_change_drops_old_capacity_link(family, change):
    runtime, requests, _, _ = family
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    key = runtime.visible[parent.rid]
    changed = {
        "session": replace(key, session_id="other"),
        "generation": replace(key, session_generation=2),
        "epoch": replace(key, context_epoch=2),
    }[change]
    assert runtime._live_parent_capacity_handoff(changed) is None
    assert "ctx-parent" not in runtime._parent_capacity_handoffs


def test_parent_handoff_respects_aged_ordinary_admission_quota(family):
    runtime, requests, _, _ = family
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    ordinary = requests["ordinary"]
    runtime._visible_since[ordinary.rid] -= 11.
    runtime._prefetch_priority_normal_admissions = 3
    assert select(runtime, [ordinary, parent]).candidates[0] is ordinary
    runtime.on_prefill_candidate_result(ordinary, admitted=True, result="ok")
    assert select(runtime, [ordinary, parent]).candidates[0] is parent
    runtime.on_prefill_candidate_result(parent, admitted=True, result="ok")
    assert select(runtime, [ordinary, parent]).candidates[0] is ordinary


def test_existing_restore_lease_priority_can_consume_parent_capacity_link(family):
    runtime, requests, _, _ = family
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    old = runtime.visible["parent"]
    parent = parent_request(runtime, requests["parent"])
    runtime._prefetch_service_leases["h2d"] = _PrefetchServiceLease(
        old, "h2d", 11, 1, "join_ticket", None, 0., 1.,
        (("kv", 40),), demand_ready=True,
    )
    runtime._visible_since[requests["ordinary"].rid] -= 11.
    assert select(runtime, [requests["ordinary"], parent]).candidates[0] is parent
    assert runtime._prefetch_priority_promoted == parent.rid
    assert runtime._parent_handoff_priority_promoted is None
    runtime._prefetch_service_leases.clear()
    runtime.on_batch_completed(NS(reqs=[parent]))
    assert runtime.counts["parent_capacity_handoff_first_service"] == 1


def test_pending_native_dependency_retries_without_losing_sibling_accounting(family):
    runtime, _, cache, _ = family
    cache.retire_beliefkv_child_session.side_effect = (
        reclaimed(retry=((11, 1),)), reclaimed(device={0: 8}),
    )
    runtime.on_events((returned(7, "child1"),))
    assert len(runtime._terminal_child_reclaims) == 1
    assert runtime.idle_poll_timeout_ms() == 50
    runtime._retry_terminal_child_reclaims(now_ms=100.)
    assert runtime._terminal_child_reclaims == {}
    assert cache.retire_beliefkv_child_session.call_args.kwargs == {
        "session_id": "s-child1", "session_generation": 1,
        "retry_anchors": ((11, 1),), "max_nodes": 16,
    }
    assert runtime._parent_capacity_handoffs["ctx-parent"].freed_device_units == {0: 8}


@pytest.mark.parametrize("ordinary_admissions,slots", ((3, 2), (4, 1), (4, 2)))
def test_capacity_handoff_restores_one_unlocked_parent_after_ordinary_quota(
    family, ordinary_admissions, slots,
):
    runtime, requests, cache, _ = family
    runtime.enable_execution_handoff = True
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    cache.inspect_beliefkv_reentry.return_value = {
        "missing_full_tokens": 4, "missing_mamba_slots": 0,
        "device_checkpoint_tokens": 0, "checkpoint_tokens": 4,
        "reusable_input_tokens": 4, "component_leaves": ((0, ((11, 1),)),),
    }
    runtime._prefetch_priority_normal_admissions = ordinary_admissions
    prefer_parent = ordinary_admissions == 4
    expected = parent if prefer_parent else requests["ordinary"]
    step = PrefetchLoadStep(runtime.visible[expected.rid], 11, 1, 4, 0)
    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        slots, 1024 ** 3, source="native_next_prefill",
    )), patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        return_value=NS(step=step, fits_current_free_lists=True),
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore"):
        runtime.dispatch_execution_handoff(
            [requests["ordinary"], parent], running_batch=NS(reqs=[NS()]),
        )
    assert runtime._execution_handoff.key.request_id == expected.rid
    assert runtime.counts["parent_capacity_handoff_restore_selected"] == int(prefer_parent)
    assert runtime.counts["parent_capacity_handoff_restore_lookahead"] == int(
        prefer_parent and slots == 1
    )


@pytest.mark.parametrize("unavailable", ("no_slot", "no_capacity", "no_checkpoint", "waiting_join"))
def test_parent_restore_lookahead_keeps_ordinary_service_when_unavailable(family, unavailable):
    runtime, requests, cache, _ = family
    runtime.enable_execution_handoff = True
    runtime.on_events((returned(7, "child1"),))
    if unavailable != "waiting_join":
        runtime.on_events((returned(8, "child2"),))
    parent = parent_request(runtime, requests["parent"])
    cache.inspect_beliefkv_reentry.return_value = {
        "missing_full_tokens": 4, "missing_mamba_slots": 0,
        "device_checkpoint_tokens": 0, "checkpoint_tokens": 4,
        "reusable_input_tokens": 4, "component_leaves": ((0, ((11, 1),)),),
    }
    if unavailable == "no_checkpoint":
        cache.inspect_beliefkv_reentry.side_effect = (
            lambda request: None if request is parent
            else cache.inspect_beliefkv_reentry.return_value
        )
    runtime._prefetch_priority_normal_admissions = 4
    ordinary = requests["ordinary"]

    def opportunity(_, anchors, **kwargs):
        return NS(
            step=PrefetchLoadStep(anchors.key, 11, 1, 4, 0),
            fits_current_free_lists=(
                anchors.key.request_id != parent.rid or unavailable != "no_capacity"
            ),
        )

    with patch.object(runtime, "_current_residency_budget", return_value=PrefetchResidencyBudget(
        0 if unavailable == "no_slot" else 1, 1024 ** 3, source="native_next_prefill",
    )), patch(
        "beliefkv.runtime.sglang_v0520_runtime.inspect_session_h2d_opportunity",
        side_effect=opportunity,
    ), patch.object(runtime, "issue_prefetch_gpu_step", return_value="restore") as issue:
        runtime.dispatch_execution_handoff(
            [ordinary, parent], running_batch=NS(reqs=[NS()]),
        )
    if unavailable == "no_slot":
        issue.assert_not_called()
        assert runtime._execution_handoff is None
    else:
        assert runtime._execution_handoff.key.request_id == ordinary.rid
        assert issue.call_args.args[0].key.request_id == ordinary.rid
    assert runtime.counts["parent_capacity_handoff_restore_lookahead"] == 0


def test_full_transfer_ledger_does_not_consume_parent_restore_attempt(family):
    runtime, requests, _, _ = family
    runtime.enable_execution_handoff = True
    runtime.on_events((returned(7, "child1"), returned(8, "child2")))
    parent = parent_request(runtime, requests["parent"])
    runtime._prefetch_priority_normal_admissions = 4
    ledger = NS(pending_action_count=lambda _: 0, pending_count=32, max_pending=32)
    with patch.object(runtime, "physical_ledger", ledger), \
         patch.object(runtime, "plan_native_prefill") as plan, \
         patch.object(runtime, "issue_prefetch_gpu_step") as issue:
        runtime.dispatch_execution_handoff(
            [requests["ordinary"], parent], running_batch=NS(reqs=[NS()]),
        )
    plan.assert_not_called()
    issue.assert_not_called()
    assert runtime._execution_handoff is None
    assert runtime._execution_handoff_attempted == set()


@pytest.mark.parametrize("persistent", ("invocation", "context"))
def test_persistent_child_is_not_retired(family, persistent):
    runtime, _, cache, _ = family
    if persistent == "invocation":
        runtime.graph.invocations["child1"].persistent = True
    else:
        runtime.graph.contexts["ctx-child1"].persistent = True
    runtime.on_events((returned(7, "child1"),))
    cache.retire_beliefkv_child_session.assert_not_called()


def test_real_harness_persistent_child_is_reclaimed_after_context_retirement(family):
    runtime, _, cache, _ = family
    runtime.graph.invocations["child1"].persistent = True
    runtime.graph.contexts["ctx-child1"].persistent = True
    runtime.on_events((replace(returned(7, "child1"), attributes={"context_retired": True}),))
    cache.retire_beliefkv_child_session.assert_called_once_with(
        session_id="s-child1", session_generation=1,
    )
    assert runtime._parent_capacity_handoffs["ctx-parent"].freed_device_units == {0: 8, 2: 1}


def test_context_with_another_live_invocation_is_not_retired(family):
    runtime, _, cache, _ = family
    runtime.on_events((event(
        7, RuntimeEventKind.INVOCATION_CREATE, invocation_id="reuser",
        context_id="ctx-child1", parent_invocation_id="parent",
    ),))
    runtime.on_events((replace(returned(8, "child1"), attributes={"context_retired": True}),))
    cache.retire_beliefkv_child_session.assert_not_called()


def test_root_return_does_not_retire_a_child_session(family):
    runtime, _, cache, _ = family
    runtime.on_events((returned(7, "parent"),))
    cache.retire_beliefkv_child_session.assert_not_called()


@pytest.mark.parametrize("boundary", (RuntimeEventKind.CONTEXT_COMPACT, RuntimeEventKind.WORKFLOW_END))
def test_parent_capacity_link_is_cleared_at_invalidation_boundary(family, boundary):
    runtime, _, cache, _ = family
    cache.retire_beliefkv_child_session.return_value = reclaimed(retry=((11, 1),))
    cache.retire_beliefkv_child_session.side_effect = None
    runtime.on_events((returned(7, "child1"),))
    attrs = (
        {"invocation_id": "parent", "context_id": "ctx-parent", "context_epoch": 1}
        if boundary is RuntimeEventKind.CONTEXT_COMPACT else {}
    )
    runtime.on_events((event(8, boundary, **attrs),))
    assert runtime._parent_capacity_handoffs == {}
    if boundary is RuntimeEventKind.WORKFLOW_END:
        assert runtime._terminal_child_reclaims == {}
