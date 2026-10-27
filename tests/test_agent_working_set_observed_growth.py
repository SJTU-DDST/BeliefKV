from dataclasses import replace
from types import SimpleNamespace as NS
import pytest

from tests.test_agent_working_set_runtime import runtime
from tests.test_paper_baseline_runtime import request


@pytest.mark.parametrize("strategy", ("thunderagent", "thunderagent_retained"))
def test_new_agent_admission_charges_existing_observed_decode_growth(
    monkeypatch, tmp_path, strategy,
):
    live = runtime(monkeypatch, tmp_path, capacity=1000, strategy=strategy)
    try:
        first = request("one", "a", tokens=400)
        first.output_ids = []
        live.register_visible_request(first)
        live.on_prefill_candidate_result(first, admitted=True, result="CONTINUE")
        agents = live._agent_admission
        assert agents.policy.remaining_capacity() == 500

        first.output_ids.extend(range(400))
        second = request("two", "b", tokens=300)
        live.register_visible_request(second)

        assert agents.policy.agents["a"].tokens == 800
        assert agents.policy.agents["a"].active
        assert agents.policy.agents["a"].in_flight
        assert not agents.policy.allowed("b")
        assert agents.policy.remaining_capacity() == 100
        assert agents.counts["streaming_progress_checks"] == 1
        assert agents.counts["streaming_progress_updates"] == 1
        assert not live.physical_ledger.pending_count
    finally:
        live.close()


@pytest.mark.parametrize("strategy", ("thunderagent", "thunderagent_retained"))
def test_growth_refresh_uses_five_second_cadence_and_defers_running_pause(
    monkeypatch, tmp_path, strategy,
):
    live = runtime(monkeypatch, tmp_path, capacity=500, strategy=strategy)
    try:
        req = request("one", "a", tokens=300)
        live.register_visible_request(req)
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        agents = live._agent_admission
        start = agents.policy.next_control
        agents.tick(now=start)
        item = agents.policy.agents["a"]
        assert item.tokens == 302 and not item.marked_for_pause
        rounds = agents.counts["streaming_progress_rounds"]

        req.output_ids.extend(range(298))
        for offset in (1., 2., 3., 4.):
            agents.tick(now=start + offset)
            assert item.tokens == 302
        assert agents.counts["streaming_progress_rounds"] == rounds

        agents.tick(now=start + 5.)
        assert item.tokens == 600
        assert item.active and item.in_flight and item.marked_for_pause
        assert agents.counts["streaming_progress_checks"] == 2
        live.on_batch_completed(NS(reqs=[req]))
        assert not item.active
        assert not agents._live_requests
    finally:
        live.close()


@pytest.mark.parametrize("invalid", ("paused", "stale", "completed"))
def test_refresh_ignores_nonlive_consumers(monkeypatch, tmp_path, invalid):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        req = request("one", "a", tokens=300)
        live.register_visible_request(req)
        agents = live._agent_admission
        item = agents.policy.agents["a"]
        req.output_ids.extend(range(50))
        if invalid == "paused":
            agents.policy._pause(item, 0.)
        elif invalid == "stale":
            live.visible[req.rid] = replace(live.visible[req.rid], attempt_id=1)
        else:
            live.on_batch_completed(NS(reqs=[req]))
        before = item.tokens
        agents._refresh_streaming_progress()
        assert item.tokens == before
        assert agents.counts["streaming_progress_checks"] == 0
    finally:
        live.close()


def test_requeued_native_attempt_keeps_one_current_progress_binding(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path, capacity=1000, strategy="thunderagent_retained")
    try:
        req = request("one", "a", tokens=300)
        live.register_visible_request(req)
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        req.cache_request_handle.attempt_id = 1
        live.on_requests_requeued([req], is_retracted=True)
        agents = live._agent_admission
        assert agents.requests[req.rid] == live.visible[req.rid]
        assert agents._live_requests == {req.rid: req}
        req.output_ids.extend(range(50))
        agents._refresh_streaming_progress()
        assert agents.policy.agents["a"].tokens == 352
        assert agents.counts["registered"] == 1
        assert len(agents.policy.agents) == 1
    finally:
        live.close()
    assert not agents._live_requests


@pytest.mark.parametrize("retirement", ("forget", "abort", "close"))
def test_retirement_releases_progress_references(monkeypatch, tmp_path, retirement):
    live = runtime(monkeypatch, tmp_path, strategy="thunderagent_retained")
    try:
        req = request("one", "a", tokens=300)
        live.register_visible_request(req)
        agents = live._agent_admission
        if retirement == "forget":
            agents.forget("a", now=0.)
        elif retirement == "abort":
            agents.aborted(req.rid)
        else:
            agents.close()
        assert not agents._live_requests
        assert not agents.requests
    finally:
        live.close()


def test_request_boundary_working_set_keeps_its_declared_accounting(monkeypatch, tmp_path):
    live = runtime(monkeypatch, tmp_path, strategy="working_set")
    try:
        req = request("one", "a", tokens=300)
        live.register_visible_request(req)
        agents = live._agent_admission
        req.output_ids.extend(range(50))
        agents.tick(now=0.)
        assert agents.policy.agents["a"].tokens == 300
        assert agents.status()["streaming_context_accounting"] == "request_boundaries_only"
    finally:
        live.close()
