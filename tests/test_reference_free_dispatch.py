"""Exercise bounded logical dispatch through the real Full lifecycle."""

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import MODES, arm_environment
from beliefkv.experiments.paper_baselines import environment as paper_environment
from beliefkv.runtime.reference_thunder_admission import validate_reference_admission
from scripts.run_causal_comparison import preflight_runtime
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request
from tests.test_reference_thunder_admission import full_runtime


def queue(monkeypatch, tmp_path, *, count=10, tokens=100, capacity=1000):
    live = full_runtime(
        monkeypatch, tmp_path, capacity=capacity,
        free_continuation_admission=True, free_dispatch_interval_ms=500,
    )
    live.on_events((observed(0, Kind.WORKFLOW_START), *(
        observed(index + 1, Kind.INVOCATION_CREATE, invocation_id=name, context_id=name + "-context")
        for index, name in enumerate(("peer", *(f"a{i}" for i in range(count))))
    )))
    peer = request("peer-rid", "peer", tokens=capacity - 100, workflow="wf")
    peer.output_ids.clear()
    live.register_visible_request(peer)
    adapter = live._agent_admission
    adapter.next_tick = float("inf")
    requests = []
    for index in range(count):
        req = request(f"rid-{index}", f"a{index}", tokens=tokens, workflow="wf")
        req.output_ids.clear()
        live.register_visible_request(req)
        requests.append(req)
    assert adapter.policy.programs["peer"].active
    assert all(not adapter.policy.programs[f"a{i}"].active for i in range(count))
    assert len(adapter._dispatch_pending) == count
    return live, adapter, peer, requests


def test_release_event_admits_first_calls_without_waiting_for_five_second_tick(monkeypatch, tmp_path):
    live, adapter, _peer, _reqs = queue(monkeypatch, tmp_path)
    try:
        records = []
        with monkeypatch.context() as patch:
            patch.setattr(live, "_opportunity_writer", NS(record=records.append))
            live.on_events((observed(
                20, Kind.INVOCATION_CANCEL, invocation_id="peer", context_id="peer-context",
            ),))
        assert sum(item.active for item in adapter.policy.programs.values()) == 4
        assert adapter.policy.remaining_capacity() == 200
        assert adapter.counts["control_ticks"] == 0
        assert adapter.counts["free_dispatch_new_programs_admitted"] == 4
        grants = [row for row in records if row["event"] == "agent_dispatch_free_capacity_admitted"]
        assert len(grants) == 4
        assert all(row["required_tokens"] == 200 for row in grants)
        assert all(row["native_capacity_authority_preserved"] for row in grants)
    finally:
        live.close()


def test_half_second_fallback_fills_more_than_one_budget_without_switching_active_agents(
    monkeypatch, tmp_path,
):
    live, adapter, _peer, _reqs = queue(monkeypatch, tmp_path)
    try:
        adapter.forget("peer")
        adapter.tick(now=100.)
        first = {item.identity for item in adapter.policy.programs.values() if item.active}
        assert len(first) == 4
        passes = adapter.counts["free_dispatch_passes"]
        for index in range(1, 50):
            adapter.tick(now=100. + index / 100.)
        assert adapter.counts["free_dispatch_passes"] == passes
        adapter.tick(now=100.5)
        current = {item.identity for item in adapter.policy.programs.values() if item.active}
        assert first < current and len(current) == 5
        assert not any(item.marked_for_pause for item in adapter.policy.programs.values())
        assert adapter.counts["control_ticks"] == 0
        assert adapter.counts["free_dispatch_fallback_ticks"] == 2
        assert adapter.policy.remaining_capacity() == 0
    finally:
        live.close()


def test_rotating_pending_index_reaches_small_candidate_beyond_eight_unfitting_agents(
    monkeypatch, tmp_path,
):
    live, adapter, peer, requests = queue(monkeypatch, tmp_path, count=9, tokens=400)
    try:
        # Observed compaction leaves a 300-token raw budget while eight older
        # pending programs still each need 500 tokens.
        peer.origin_input_ids = peer.origin_input_ids[:600]
        adapter.policy.programs["peer"].tokens = 600
        small = requests[-1]
        small.origin_input_ids = small.origin_input_ids[:100]
        adapter.policy.programs["a8"].tokens = 100
        adapter._dispatch_pending.move_to_end("a8")
        adapter.tick(now=100.)
        assert not adapter.policy.programs["a8"].active
        checks = adapter.counts["free_dispatch_candidate_checks"]
        adapter.tick(now=100.5)
        assert adapter.policy.programs["a8"].active
        assert adapter.counts["free_dispatch_candidate_checks"] - checks <= 8
        assert adapter.policy.programs["peer"].active
        assert not adapter.policy.programs["peer"].marked_for_pause
    finally:
        live.close()


@pytest.mark.parametrize("change", ("marked", "stale_visible", "epoch", "tool", "terminal"))
def test_dispatch_never_overrides_identity_or_executability(monkeypatch, tmp_path, change):
    live, adapter, _peer, _reqs = queue(monkeypatch, tmp_path, count=1)
    try:
        adapter.forget("peer")
        key = adapter.latest["a0"]
        if change == "marked":
            adapter.policy.programs["a0"].marked_for_pause = True
        elif change == "stale_visible":
            live.visible[key.request_id] = replace(key, context_epoch=1)
        elif change == "epoch":
            live.graph.contexts[key.context_id].epoch += 1
        elif change == "tool":
            live.graph.invocations["a0"].active_tool_calls["t"] = "execute"
        else:
            from beliefkv.control.causal_graph import InvocationState

            live.graph.invocations["a0"].state = InvocationState.DONE
        adapter.tick(now=100.)
        assert not adapter.policy.programs["a0"].active
        assert not adapter._dispatch_pending
    finally:
        live.close()


def test_dispatch_refreshes_observed_growth_before_grant(monkeypatch, tmp_path):
    live, adapter, peer, _reqs = queue(monkeypatch, tmp_path, count=1, capacity=800)
    try:
        peer.origin_input_ids = peer.origin_input_ids[:500]
        adapter.policy.programs["peer"].tokens = 500
        peer.output_ids.extend([1] * 10)
        adapter.tick(now=100.)
        assert adapter.policy.programs["peer"].tokens == 510
        assert not adapter.policy.programs["a0"].active
        assert adapter.policy.remaining_capacity() == 190
    finally:
        live.close()


def test_known_ready_join_precedes_ordinary_pending_members(monkeypatch, tmp_path):
    live, adapter, _peer, _reqs = queue(monkeypatch, tmp_path, count=6)
    try:
        adapter.forget("peer")
        live._join_priority_pending["a5"] = "join"
        monkeypatch.setattr(live, "_ready_join_parent", lambda req, key: key.invocation_id == "a5")
        adapter.tick(now=100.)
        assert adapter.policy.programs["a5"].active
        assert adapter.counts["free_join_continuation_admitted"] == 1
        assert sum(item.active for item in adapter.policy.programs.values()) == 4
    finally:
        live.close()


def test_declared_dispatch_keeps_pressure_reconciliation_at_five_seconds(monkeypatch, tmp_path):
    live, adapter, _peer, _reqs = queue(monkeypatch, tmp_path, count=1)
    try:
        adapter.next_tick = 0.
        adapter.tick(now=100.)
        assert adapter.next_tick == 105.
        adapter.tick(now=100.5)
        assert adapter.next_tick == 105.
        assert adapter.counts["control_ticks"] == 1
        state = adapter.status()
        validate_reference_admission(
            state, free_continuation_admission=True, free_dispatch_interval_ms=500,
        )
        with pytest.raises(ValueError):
            validate_reference_admission(state, free_continuation_admission=True)
        assert not state["free_dispatch_replaces_pressure_tick"]
        assert not state["free_continuation_physical_prefetch"]
    finally:
        live.close()


@pytest.mark.parametrize("mode", MODES)
def test_dispatch_is_explicitly_stripped_from_controls(mode):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    }, mode)
    assert settings["BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS"] == ("500" if mode == "full" else "0")


@pytest.mark.parametrize("method", ("thunderagent", "thunderagent_acting_decay", "kvflow_observed_graph"))
def test_paper_adapters_preserve_their_reference_tick(method):
    settings = paper_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    }, method)
    assert settings["BELIEFKV_AGENT_FREE_DISPATCH_INTERVAL_MS"] == "0"


@pytest.mark.parametrize("value", ("50", "5001", "nan", "-1"))
def test_invalid_dispatch_period_is_rejected(value):
    with pytest.raises(ValueError, match="bounded free dispatch"):
        arm_environment({
            "AGENT_ADMISSION": "thunderagent_reference",
            "AGENT_FREE_CONTINUATION_ADMISSION": "1",
            "AGENT_FREE_DISPATCH_INTERVAL_MS": value,
        }, "full")


def test_preflight_binds_effective_interval_and_pending_index(monkeypatch, tmp_path):
    settings = arm_environment({
        "AGENT_ADMISSION": "thunderagent_reference",
        "AGENT_FREE_CONTINUATION_ADMISSION": "1",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500",
    }, "full")
    result = preflight_runtime(settings, str(tmp_path / "preflight"))
    assert result["verified_configuration"]["agent_free_dispatch_interval_ms"] == 500
