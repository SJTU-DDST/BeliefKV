from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts.prepare_simple_path_oracle import prepare_artifact
from tests.test_internal_comparison import observed
from tests.test_subagent_materialization import cache, child, node, prepare
from tests.test_workflow_size_admission import settings
from tests.test_workflow_work_oracle import add_serial_graphs, oracle_file, source


def queued_children(monkeypatch, tmp_path, *, ranking, tool_seconds):
    raw = source()
    raw.update(
        admission_order="global_ready_remaining_path",
        free_dispatch_coalescing_window_ms=2000,
        remaining_work_ranking_enabled=True,
        requests={}, workflows={},
    )
    for name, outputs in (("long", 1000), ("short", 10)):
        workflow = name + "-work"
        raw["requests"][name + "-request"] = {
            "workflow_id": workflow, "invocation_id": name,
            "context_id": name + "-context", "prompt_tokens": 100,
            "output_tokens": outputs, "input_increment": 100,
            "predecessor_request_id": None,
        }
        raw["workflows"][workflow] = {
            "total_work_units": 100 + 100 * outputs, "requests": 1,
        }
    add_serial_graphs(raw)
    raw["workflow_graphs"]["short-work"][-1]["delay_s"] = tool_seconds
    raw = prepare_artifact(
        dict(raw, remaining_work_ranking_enabled=False), raw,
        check_interval_ms=400,
    )
    raw["remaining_work_ranking_enabled"] = ranking
    path, digest = oracle_file(tmp_path, raw)
    environment = arm_environment({
        **settings(tmp_path), "WORKFLOW_WORK_SOURCE": "oracle",
        "WORKFLOW_WORK_MODEL": path, "WORKFLOW_WORK_MODEL_SHA256": digest,
        "WORKFLOW_COMBINATION_ADMISSION": "0",
        "AGENT_FREE_DISPATCH_INTERVAL_MS": "500", "AGENT_COADMIT_JOIN_CHILDREN": "1",
        "BELIEFKV_SUBAGENT_MATERIALIZATION": "pressure_v1",
    }, "full")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "child-order.sock"))
    try:
        live.attach_native_cache(cache(node(1, 1000), device_free=251))
        live._agent_admission.next_tick = float("inf")
        live._native_running_batch = NS(reqs=[])
        live._test_sequence = 10
        requests = []
        for index, name in enumerate(("long", "short")):
            workflow, parent = name + "-work", name + "-parent"
            live.on_events((
                replace(observed(2 * index, Kind.WORKFLOW_START), workflow_id=workflow),
                replace(observed(
                    2 * index + 1, Kind.INVOCATION_CREATE,
                    invocation_id=parent, context_id=parent + "-context",
                ), workflow_id=workflow),
            ))
            requests.append(child(live, name, parent=parent, workflow=workflow))
        return live, requests
    except Exception:
        live.close()
        raise


@pytest.mark.parametrize("ranking,tool_seconds,expected", (
    (True, 0., "short"),
    (False, 0., "long"),
    (True, 100., "long"),
))
def test_initial_materialization_uses_the_same_oracle_path_order_as_actual_admission(
    monkeypatch, tmp_path, ranking, tool_seconds, expected,
):
    live, requests = queued_children(
        monkeypatch, tmp_path, ranking=ranking, tool_seconds=tool_seconds,
    )
    try:
        admission, gate = live._agent_admission, live._subagent_materialization
        assert admission._combination is None
        assert admission._tool_boundary_preemption is None
        assert all(not gate.allows(live.visible[req.rid]) for req in requests)
        short = admission._work.prediction("short-work")
        long = admission._work.prediction("long-work")
        assert short.remaining_work < long.remaining_work
        assert (short.remaining_path_s < long.remaining_path_s) is (tool_seconds == 0.)

        prepare(live, requests, running=[object()])
        assert gate.last_decision["allowed"] == [expected + "-request"]
        assert not any(item.active for item in admission.policy.programs.values())

        admission._deferred_dispatch.next_dispatch = 1.
        plan = live.plan_native_prefill(requests, running_batch=NS(reqs=[]), adder=None)
        selected = select_native_prefill_candidates(
            requests, plan=plan, current_semantic_revision=live.semantic_revision,
        )
        assert [req.rid for req in selected.candidates] == [expected + "-request"]
        assert {
            item.identity for item in admission.policy.programs.values() if item.active
        } == {expected}
        assert all(req.kv.kv_allocated_len == 0 for req in requests)
        assert admission.policy.remaining_capacity() >= 0
    finally:
        live.close()
