from types import SimpleNamespace as NS
import time

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.policy.paper_baselines import ThunderProgram
from beliefkv.policy.workflow_size_resume import WorkflowSizeWorkingSet
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_paper_baseline_runtime import request
from tests.test_reference_sibling_coadmission import group
from tests.test_workflow_size_admission import settings


def test_readiness_is_observed_before_grant_without_changing_original_selection():
    def create(observer):
        policy = WorkflowSizeWorkingSet(
            500, buffer_tokens=0, rank=lambda owner, now: 1,
            sibling_group=lambda owner: None, observe_ready=observer,
        )
        policy.programs = {
            "ready": ThunderProgram("ready", 300, steps=2, pending=True),
            "tool": ThunderProgram("tool", 200, steps=2, acting=True),
        }
        return policy

    observed = []
    def observer(item):
        observed.append((item.identity, item.active, item.pending))
        return item.pending
    original, audited = create(None), create(observer)
    assert original.tick(now=10.) == audited.tick(now=10.)
    assert observed == [("ready", False, True), ("tool", False, False)]
    assert audited.last_selection["logical_ready_paused_before_resume"] == {"ready": True, "tool": False}
    assert original.last_selection["ordered"] == audited.last_selection["ordered"]


def test_readiness_never_scans_when_no_resume_capacity_remains():
    def forbidden(_item):
        raise AssertionError("no-budget control must not observe readiness")
    policy = WorkflowSizeWorkingSet(
        100, rank=lambda owner, now: 1, sibling_group=lambda owner: None, observe_ready=forbidden,
    )
    policy.programs = {
        "active": ThunderProgram("active", 1, active=True, pending=True),
        "paused": ThunderProgram("paused", 1, pending=True),
    }
    assert policy.tick(now=10.) == ()


def test_native_runtime_distinguishes_first_ready_calls_from_waiting_join_and_tools(monkeypatch, tmp_path):
    env = arm_environment({**settings(tmp_path), "AGENT_COADMIT_JOIN_CHILDREN": "1"}, "full")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BELIEFKV_PAPER_BASELINE", "")
    runtime = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "ready.sock"))
    try:
        runtime.attach_native_cache(NS(
            tree_components=(0,), token_to_kv_pool_allocator=NS(size_full=1000),
        ))
        group(runtime)
        runtime.on_events((
            RuntimeEvent("other-start", 0., Kind.WORKFLOW_START, "other"),
            RuntimeEvent(
                "other-peer", 1., Kind.INVOCATION_CREATE, "other",
                invocation_id="peer", context_id="peer-context", context_epoch=0,
                relation_type=RelationType.ROOT,
            ),
        ))
        for rid, invocation, workflow in (
            ("a1", "child-0", "wf"), ("b", "peer", "other"), ("a2", "child-1", "wf"),
        ):
            runtime.register_visible_request(request(rid, invocation, tokens=300, workflow=workflow))
        admission = runtime._agent_admission
        for item in admission.policy.programs.values():
            item.active = False
        item = admission.policy.programs["peer"]
        assert item.steps == 1
        key = admission.latest["peer"]
        assert admission._candidate(key.request_id, key) is None
        assert admission._logical_ready_paused(item)
        invocation = runtime.graph.invocations["peer"]
        invocation.active_tool_calls["ongoing"] = ("execute", 1.)
        assert not admission._logical_ready_paused(item)
        invocation.active_tool_calls.clear()
        invocation.blocking_child_ids.add("child")
        assert not admission._logical_ready_paused(item)
        invocation.blocking_child_ids.clear()
        admission.next_tick = 0.
        admission.tick(now=time.monotonic())
        ready = admission.policy.last_selection["logical_ready_paused_before_resume"]
        assert ready == {"child-0": True, "peer": True, "child-1": True}
    finally:
        runtime.close()
