import asyncio
from dataclasses import replace
import time

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.causal_replay import (
    CausalReplayPlan, NativeReplayCompletion, ReplayNode, execute_causal_replay,
)
from beliefkv.experiments.replay_timing import ReplayIdleClock, ReplayTimingPolicy
from beliefkv.runtime.clock_evidence import local_monotonic_clock_domain


def clock(policy=ReplayTimingPolicy(300., 10.)):
    return ReplayIdleClock(policy, {"a": "w", "b": "w", "c": "other"}, 0.)


def test_global_shift_preserves_pending_spacing_and_does_not_rewrite_source_delays():
    idle = clock()
    deadlines = {"a": 1000., "b": 1200., "c": 1300.}
    idle.adjust(deadlines, set(), 2.)
    assert deadlines == {"a": 10., "b": 210., "c": 310.}
    assert idle.summary()["system_skipped_ms"] == 990.


def test_unset_workflow_cap_preserves_external_gap_while_another_request_is_in_flight():
    idle = clock(ReplayTimingPolicy(None, 10_000.))
    idle.request_started("other")
    deadlines = {"a": 900_000., "b": 910_000.}
    idle.adjust(deadlines, {"other"}, 300_001.)
    assert deadlines == {"a": 900_000., "b": 910_000.}
    assert idle.summary()["shifts"] == []
    idle.request_finished("other", 300_001.)
    idle.adjust(deadlines, set(), 300_001.)
    assert deadlines == {"a": 310_001., "b": 320_001.}
    assert idle.summary()["system_skipped_ms"] == 589_999.
    assert idle.summary()["workflow_skipped_ms"] == {"other": 0, "w": 0}


def test_low_concurrency_never_requires_waiting_out_the_workflow_300_second_budget():
    idle = clock(ReplayTimingPolicy(300_000., 10_000.))
    deadlines = {"a": 300_000., "b": 310_000.}
    idle.adjust(deadlines, set(), 0.)
    assert deadlines == {"a": 10_000., "b": 20_000.}
    remaining = {"b": 20_000.}
    idle.adjust(remaining, set(), 10_001.)
    assert remaining == {"b": 10_001.}
    assert idle.summary()["workflow_skipped_ms"]["w"] == 0.


def test_external_timer_idle_is_accounted_separately_from_time_skipped():
    idle = clock()
    idle.observe_timer_idle_wait(0., 10.)
    idle.request_started("w")
    idle.request_finished("w", 200.)
    idle.observe_timer_idle_wait(200., 210.)
    result = idle.summary()
    assert result["no_inflight_external_timer_wait_ms"] == 20.
    assert result["no_inflight_external_timer_wait_count"] == 2
    assert result["maximum_no_inflight_external_timer_wait_ms"] == 10.
    assert result["system_skipped_ms"] == 0.


def test_queued_or_running_request_prevents_global_and_same_workflow_idle_compression():
    idle = clock()
    idle.request_started("w")
    deadlines = {"b": 1000., "c": 1200.}
    idle.adjust(deadlines, {"w"}, 301.)
    assert deadlines == {"b": 1000., "c": 301.}
    assert idle.summary()["system_skipped_ms"] == 0.
    assert idle.summary()["workflow_skipped_ms"] == {"other": 899., "w": 0}


def test_workflow_idle_cap_does_not_change_other_active_workflow_timers():
    idle = clock(ReplayTimingPolicy(300., None))
    idle.request_started("other")
    deadlines = {"a": 1000., "b": 1200., "c": 1500.}
    idle.adjust(deadlines, {"other"}, 299.)
    assert deadlines["a"] == 1000.
    assert idle.next_wakeup_ms(deadlines, {"other"}, 299.) == 1.
    idle.adjust(deadlines, {"other"}, 300.)
    assert deadlines == {"a": 300., "b": 500., "c": 1500.}


def test_parallel_children_share_the_same_idle_account_until_last_request_returns():
    idle = clock()
    idle.request_started("w")
    idle.request_started("w")
    idle.request_finished("w", 100.)
    assert idle.workflow_idle_since["w"] is None
    idle.request_finished("w", 200.)
    deadlines = {"a": 1000.}
    idle.adjust(deadlines, set(), 202.)
    assert deadlines == {"a": 210.}


def test_ready_control_work_and_pending_dependency_barriers_cannot_be_accelerated():
    idle = clock()
    deadlines = {"a": 0., "b": 1000.}
    idle.adjust(deadlines, set(), 301.)
    assert deadlines == {"a": 0., "b": 1000.}
    deadlines = {"a": 1000.}
    idle.adjust(deadlines, {"w"}, 302.)
    assert deadlines == {"a": 1000.}
    assert not idle.summary()["shifts"]


@pytest.mark.parametrize("value", (0., -1., float("nan"), float("inf"), True, "10"))
def test_invalid_runtime_idle_caps_fail(value):
    with pytest.raises(ValueError):
        ReplayTimingPolicy(value, None)


def test_actual_executor_caps_long_external_idle_but_waits_for_real_request_completion():
    def event(key, kind, dependencies=(), delay=0., **attributes):
        return ReplayNode(
            key, "w", dependencies, delay,
            event=RuntimeEvent(key, 0., kind, "w", attributes=attributes),
        )

    plan = CausalReplayPlan((
        event("start", Kind.WORKFLOW_START),
        event("submit-a", Kind.LLM_SUBMIT, ("start",), request_id="a"),
        ReplayNode("request-a", "w", ("submit-a",), 0., request_id="a"),
        event("submit-b", Kind.LLM_SUBMIT, ("request-a",), 3_600_000., request_id="b"),
        ReplayNode("request-b", "w", ("submit-b",), 0., request_id="b"),
        event("end", Kind.WORKFLOW_END, ("request-b",)),
    ), {"a": (128, 4), "b": (128, 4)}, False, 0)
    observed = []

    async def run():
        async def execute(rid, submit):
            if rid == "b":
                assert observed == ["a"]
            await asyncio.sleep(.002)
            observed.append(rid)
            return NativeReplayCompletion(
                rid, time.monotonic() * 1000., local_monotonic_clock_domain(), 128, 4,
            )

        async def emit(_):
            pass

        return await asyncio.wait_for(
            execute_causal_replay(
                plan, execute, emit, timing_policy=ReplayTimingPolicy(None, 1.),
            ), .5,
        )

    result = asyncio.run(run())
    assert observed == ["a", "b"]
    assert result["completed_requests"] == 2
    assert result["replay_timing"]["system_skipped_ms"] > 3_599_000.
    assert plan.nodes[3].delay_ms == 3_600_000.
    assert result["workflow_elapsed_ms"]["w"] >= 4.
    assert result["replay_timing"]["no_inflight_external_timer_wait_ms"] > 0.
    assert 0. < result["replay_timing"]["no_inflight_external_timer_wait_fraction"] < 1.
