"""Native v0.5.20 semantic producer never authorizes physical operations."""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind
from beliefkv.runtime.event_channel import SCHEMA_VERSION
from beliefkv.runtime.sglang_v0520_admission import select_native_prefill_candidates
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from beliefkv.runtime.sglang_v0520_physical import (
    PhysicalActionExpectation,
    PhysicalChildExpectation,
    PhysicalReceiptError,
)


def test_native_ack_is_credited_only_after_live_context_reconciliation():
    runtime = NativeAdmissionRuntime()
    tagged = req("a")
    assert runtime.register_visible_request(tagged)
    runtime.on_events((
        event(0, RuntimeEventKind.WORKFLOW_START),
        event(
            1, RuntimeEventKind.INVOCATION_CREATE,
            invocation_id="a", context_id="ctx-a",
            agent_definition_id="a", agent_instance_id="a",
        ),
    ))
    expectation = PhysicalActionExpectation(
        command_id="prepare-a",
        action="PREPARE_HOST",
        context_id="ctx-a",
        context_epoch=0,
        children=(PhysicalChildExpectation(
            anchor_node_id=11,
            published_node_ids=(11,),
            pool_bytes=(("kv", 20), ("mamba", 5)),
            num_bytes=25,
        ),),
        pool_bytes_per_token=(("kv", 10), ("mamba", 5)),
    )
    runtime.register_physical_action(expectation)
    runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
        child_commits=(NS(
            command_id="prepare-a", anchor_node_id=11,
            published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            num_bytes=25,
        ),),
    ))
    assert [action.command_id for action in runtime.completed_physical_actions] == [
        "prepare-a"
    ]
    assert runtime.counts["native_physical_completed"] == 1
    assert runtime.physical_ledger.pending_count == 0
    runtime.on_native_transfer_commit(NS(
        direction="d2h", status="completed", node_ids=(11,),
        num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
        child_commits=(NS(
            command_id="prepare-a", anchor_node_id=11,
            published_node_ids=(11,),
            num_tokens_by_pool=(("kv", 2), ("mamba", 1)),
            num_bytes=25,
        ),),
    ))
    assert runtime.physical_disabled
    assert runtime.counts["physical_receipt_failed"] == 1
    with pytest.raises(PhysicalReceiptError, match="no live causal context"):
        runtime.register_physical_action(expectation)


def req(name: str, *, tagged: bool = True):
    return NS(
        rid=name,
        beliefkv_metadata=(
            {
                "root_workflow_id": "wf",
                "invocation_id": name,
                "context_id": f"ctx-{name}",
                "context_epoch": 0,
            }
            if tagged
            else None
        ),
        cache_request_handle=NS(attempt_id=0),
        session_id=None,
        session_generation=None,
        finished=lambda: False,
    )


def event(seq: int, kind: RuntimeEventKind, **kwargs):
    return RuntimeEvent(
        event_id=f"e{seq}", ts_ms=float(seq), kind=kind, workflow_id="wf", **kwargs
    )


def select(runtime, native):
    plan = runtime.plan_native_prefill(native, running_batch=None, adder=None)
    return select_native_prefill_candidates(
        native, plan=plan, current_semantic_revision=runtime.semantic_revision
    )


def test_native_fallback_and_causal_join_straggler_ranking():
    runtime = NativeAdmissionRuntime()
    a, plain, b = req("a"), req("plain", tagged=False), req("b")
    assert runtime.register_visible_request(a)
    assert runtime.register_visible_request(b)
    assert select(runtime, [a, plain, b]).candidates == (a, plain, b)
    runtime.on_events(
        (
            event(0, RuntimeEventKind.WORKFLOW_START),
            event(
                1, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="a", context_id="ctx-a",
                agent_definition_id="a", agent_instance_id="a",
            ),
            event(
                2, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="b", context_id="ctx-b",
                agent_definition_id="b", agent_instance_id="b",
            ),
            event(
                3, RuntimeEventKind.INVOCATION_CREATE,
                invocation_id="waiter", context_id="ctx-waiter",
                agent_definition_id="waiter", agent_instance_id="waiter",
            ),
            event(
                4, RuntimeEventKind.JOIN_CREATE,
                join_id="join", member_invocation_ids=("b",),
            ),
            event(
                5, RuntimeEventKind.JOIN_WAIT,
                invocation_id="waiter", join_id="join",
            ),
        )
    )
    assert select(runtime, [a, plain, b]).candidates == (b, plain, a)
    assert runtime.visible == {"a": runtime.visible["a"], "b": runtime.visible["b"]}
    assert select(runtime, [a, plain, b]).rejected == ()


def test_stale_attempt_and_session_fail_closed_without_blocking_native():
    runtime = NativeAdmissionRuntime()
    tagged, plain = req("tagged"), req("plain", tagged=False)
    runtime.register_visible_request(tagged)
    plan = runtime.plan_native_prefill([tagged, plain], running_batch=None, adder=None)
    tagged.cache_request_handle.attempt_id += 1
    result = select_native_prefill_candidates(
        [tagged, plain], plan=plan,
        current_semantic_revision=runtime.semantic_revision,
    )
    assert result.candidates == (plain,)
    assert result.rejected == (("tagged", "identity_changed"),)
    runtime.on_requests_requeued([tagged], is_retracted=True)
    assert select(runtime, [tagged, plain]).candidates == (tagged, plain)
    tagged.session_id = "s1"
    tagged.session_generation = 1
    runtime.on_requests_requeued([tagged], is_retracted=True)
    plan = runtime.plan_native_prefill([tagged], running_batch=None, adder=None)
    tagged.session_generation = 2
    result = select_native_prefill_candidates(
        [tagged], plan=plan,
        current_semantic_revision=runtime.semantic_revision,
    )
    assert result.rejected == (("tagged", "identity_changed"),)


def test_fresh_plan_after_abort_and_completion_does_not_authorize_old_request():
    runtime = NativeAdmissionRuntime()
    first, second = req("first"), req("second")
    runtime.register_visible_request(first)
    runtime.register_visible_request(second)
    revision = runtime.semantic_revision
    runtime.on_abort_request(NS(rid="first", abort_all=False))
    assert runtime.semantic_revision > revision
    assert select(runtime, [first, second]).candidates == (second,)
    assert select(runtime, [first, second]).rejected == (
        ("first", "no_authorization"),
    )
    # Queue ownership stays with native SGLang; abort removes the native
    # waiting request separately. A ghost native request must not be admitted.
    second.finished = lambda: True
    runtime.on_batch_completed(NS(reqs=[second]))
    assert "second" not in runtime.visible


def test_bounded_or_invalid_tagged_candidates_cannot_starve_untagged():
    runtime = NativeAdmissionRuntime()
    invalid, plain = req("invalid"), req("plain", tagged=False)
    invalid.beliefkv_metadata["context_epoch"] = True
    assert runtime.register_visible_request(invalid) is False
    assert select(runtime, [invalid, plain]).candidates == (plain,)
    many = [req(f"r{index}") for index in range(512)]
    for item in many:
        runtime.register_visible_request(item)
    beyond = req("beyond")
    runtime.register_visible_request(beyond)
    selection = select(runtime, many + [plain, beyond])
    assert selection.candidates == (*many, plain)
    assert selection.rejected == (("beyond", "no_authorization"),)


def test_partial_event_failure_discards_mirror_and_restores_native_order():
    runtime = NativeAdmissionRuntime()
    a, b = req("a"), req("b")
    runtime.register_visible_request(a)
    runtime.register_visible_request(b)
    with pytest.raises(Exception):
        runtime.on_events(
            (
                event(0, RuntimeEventKind.WORKFLOW_START),
                event(1, RuntimeEventKind.RETURN, invocation_id="missing"),
            )
        )
    assert runtime.graph.invocations == {}
    assert runtime.counts["causal_mirror_discarded"] == 1
    assert select(runtime, [a, b]).candidates == (a, b)


def test_terminal_workflow_is_removed_before_native_prefill():
    runtime = NativeAdmissionRuntime()
    tagged, plain = req("tagged"), req("plain", tagged=False)
    runtime.register_visible_request(tagged)
    runtime.on_events(
        (
            event(0, RuntimeEventKind.WORKFLOW_START),
            event(1, RuntimeEventKind.WORKFLOW_END),
        )
    )
    assert runtime.terminal_waiting_request_ids([tagged, plain]) == ("tagged",)
    assert select(runtime, [tagged, plain]).candidates == (plain,)
    runtime.retire_terminal_request("tagged")
    assert "tagged" not in runtime.visible


def test_scheduler_step_drains_causal_socket_and_close_releases_path(tmp_path):
    path = tmp_path / "causal.sock"
    runtime = NativeAdmissionRuntime(event_socket_path=str(path))
    message = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "message_id": "one",
            "events": [event(0, RuntimeEventKind.WORKFLOW_START).to_dict()],
        }
    ).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
        sender.sendto(message, str(path))
    runtime.scheduler_step()
    assert "wf" in runtime.graph.workflows
    assert runtime.semantic_revision == 1
    runtime.close()
    assert not path.exists()
