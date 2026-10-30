import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from beliefkv.predictor.workflow_rank_input import (
    DELEGATION_CHARS, ROLE_CHARS, TASK_CHARS, WorkflowRankInput,
)
from beliefkv.runtime.deepagents_adapter import DeepAgentsRuntimeAdapter
from beliefkv.runtime.sglang_adapter import BeliefKVRequestMetadata
from tests.test_deepagents_adapter import CollectingSink


def test_semantic_metadata_has_strict_caps_and_never_accepts_prompt_or_future_fields():
    item = WorkflowRankInput.from_instructions(
        task_instructions="a" * (TASK_CHARS + 1),
        delegation_instructions="b" * (DELEGATION_CHARS + 1), agent_role="c" * (ROLE_CHARS + 1),
    )
    assert item.task_truncated and item.delegation_truncated and item.role_truncated
    assert len(item.task_instructions) == TASK_CHARS
    assert len(item.delegation_instructions) == DELEGATION_CHARS
    assert len(item.agent_role) == ROLE_CHARS
    assert WorkflowRankInput.from_dict(item.to_dict()) == item
    for name in ("prompt", "prompt_tokens", "remaining_work", "future_tools", "source_code"):
        with pytest.raises(ValueError):
            WorkflowRankInput.from_dict({**item.to_dict(), name: "forbidden"})


def test_direct_adapter_records_only_explicit_root_and_delegation_instructions():
    sink = CollectingSink()
    root = BeliefKVRequestMetadata("wf", "root", "ctx", 0, agent_definition_id="supervisor")
    adapter = DeepAgentsRuntimeAdapter(sink, root)
    adapter.start(task_instructions="Investigate a single bug.")
    adapter.declare_runtime_tasks([("review-agent", "Verify the boundary condition.")])
    start = next(row for row in sink.events if row.kind is Kind.WORKFLOW_START)
    text = WorkflowRankInput.from_dict(start.attributes["workflow_ranking_input"])
    assert text.task_instructions == "Investigate a single bug."
    child = next(
        row for row in sink.events if row.kind is Kind.INVOCATION_CREATE and row.parent_invocation_id
    )
    delegated = WorkflowRankInput.from_dict(child.attributes["workflow_ranking_input"])
    assert delegated.delegation_instructions == "Verify the boundary condition."
    assert delegated.agent_role == "review-agent"
    assert delegated.task_instructions == ""


def test_invalid_root_input_does_not_partially_start_workflow():
    sink = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(sink, BeliefKVRequestMetadata("wf", "root", "ctx", 0))
    with pytest.raises(ValueError):
        adapter.start(task_instructions=["full", "prompt"])
    assert sink.events == []
    adapter.start(task_instructions="An explicit task.")


def test_four_member_unicode_group_stays_within_control_datagram_budget():
    from beliefkv.runtime.event_channel import MAX_DATAGRAM_BYTES, _encode_event_message

    sink = CollectingSink()
    adapter = DeepAgentsRuntimeAdapter(sink, BeliefKVRequestMetadata("wf", "root", "ctx", 0))
    adapter.start()
    start = len(sink.events)
    adapter.declare_runtime_tasks([
        ("review", "\u6d4b" * (DELEGATION_CHARS * 2)) for _ in range(4)
    ])
    payload = _encode_event_message("test-group", (tuple(sink.events[start:]),))
    assert len(payload) < MAX_DATAGRAM_BYTES


@pytest.mark.parametrize("name", ("task_truncated", "delegation_truncated", "role_truncated"))
def test_semantic_flags_must_be_boolean(name):
    raw = WorkflowRankInput.from_instructions(task_instructions="Task").to_dict()
    raw[name] = 1
    with pytest.raises(ValueError):
        WorkflowRankInput.from_dict(raw)
