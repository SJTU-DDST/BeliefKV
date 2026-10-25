from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RelationType, RuntimeEventKind as Kind
from beliefkv.experiments.internal_comparison import arm_environment
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from tests.test_full_only_native_lifecycle import full_native
from tests.test_internal_comparison import observed
from tests.test_paper_baseline_runtime import request


@pytest.mark.parametrize("full_native", (512,), indirect=True)
def test_agent_join_yield_preserves_real_backup_ack_and_full_restore_ownership(
    full_native, monkeypatch, tmp_path,
):
    from test_beliefkv_terminal_capacity import insert_session

    cache, allocator, writes, loads, _ = full_native
    for name, value in arm_environment({
        "AGENT_ADMISSION": "working_set", "TOOL_RESTORE_MODE": "execution",
    }, "full").items():
        monkeypatch.setenv(name, value)
    live = NativeAdmissionRuntime(event_socket_path=str(tmp_path / "native.sock"))
    live.attach_native_cache(cache)
    try:
        live.on_events((
            observed(0, Kind.WORKFLOW_START),
            observed(1, Kind.INVOCATION_CREATE, invocation_id="parent", context_id="parent-context"),
            observed(2, Kind.INVOCATION_CREATE, invocation_id="child", context_id="child-context",
                     parent_invocation_id="parent", relation_type=RelationType.SPAWN),
            observed(3, Kind.JOIN_CREATE, join_id="join", member_invocation_ids=("child",)),
        ))
        node, generation = insert_session(cache, list(range(8)), "parent-session")
        req = request("parent-first", "parent", tokens=8, workflow="wf")
        req.session_id, req.session_generation = "parent-session", generation
        live.register_visible_request(req)
        live.on_prefill_candidate_result(req, admitted=True, result="CONTINUE")
        live.on_batch_completed(NS(reqs=[req]))
        live.on_events((observed(4, Kind.JOIN_WAIT, invocation_id="parent", join_id="join"),))
        agents = live._agent_admission
        key = agents.latest["parent"]
        assert not agents.policy.agents["parent"].active
        free = allocator.available_size()
        result = cache.prepare_beliefkv_spawn_full(
            session_id="parent-session", session_generation=generation,
        )
        assert result["issued"] and len(writes) == 1
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=free + 8, mamba_slots=0,
        ) == {}
        assert allocator.available_size() == free
        cache.writing_check(finish_count=1)
        assert cache.reclaim_beliefkv_handoff_capacity(
            full_tokens=free + 8, mamba_slots=0,
        ) == {0: 8}
        assert allocator.available_size() == free + 8
        assert agents.claim_restore(key, source="join_ticket")
        outcomes = cache.prefetch_gpu_session_nodes(
            session_id=key.session_id, session_generation=key.session_generation,
            leaf_node_id=node.id, leaf_creation_time=float(node.creation_time),
            nodes=((node.id, float(node.creation_time), False, "agent-restore"),),
            beliefkv_before_enqueue=lambda _op: agents.holds_restore(key),
        )
        assert len(outcomes) == 1 and outcomes[0].issued and len(loads) == 1
        assert allocator.available_size() == free
        assert not agents.policy.allowed("parent")
        cache.loading_check(finish_count=1)
        assert node.component_data[0].value is not None
        live.on_events((observed(5, Kind.RETURN, invocation_id="child"),))
        successor = request("parent-next", "parent", tokens=8, workflow="wf")
        successor.session_id, successor.session_generation = key.session_id, key.session_generation
        live.register_visible_request(successor)
        live.on_prefill_candidate_result(successor, admitted=True, result="CONTINUE")
        assert agents.policy.restore_owner is None
        assert agents.policy.agents["parent"].in_flight
    finally:
        live.close()
