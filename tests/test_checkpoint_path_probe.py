import json
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from numpy import float64

from beliefkv.runtime.checkpoint_path_probe import checkpoint_path_evidence
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry


class NoTensorAccess:
    def __bool__(self):
        raise AssertionError("tensor truth value must not be read")

    def __len__(self):
        raise AssertionError("tensor contents must not be read")


def fixture():
    root = NS(id=0)
    arena = {0: root}
    parent = root
    for index, length in enumerate((4, 4, 2), 1):
        state = NS(
            value=NoTensorAccess(), host_value=NoTensorAccess() if index == 2 else None,
            session_ref=int(index == 1), lock_ref=0, host_lock_ref=0,
        )
        node = NS(
            id=index, creation_time=float64(index), parent=parent, key=range(length),
            component_data=[None, None, state], children={},
        )
        arena[index] = node
        parent = node
    cache = NS(tree_core=NS(root_node=root, node_by_id=arena.__getitem__))
    req = NS(last_node=3, origin_input_ids=range(8), output_ids=range(3))
    return req, cache, arena


def test_probe_separates_input_boundary_and_output_and_preserves_identity():
    req, cache, arena = fixture()
    result = checkpoint_path_evidence(req, cache)
    assert result["status"] == "observed"
    assert [row["role"] for row in result["mamba_states"]] == [
        "input_prefix", "input_boundary", "generated_output_tail",
    ]
    assert [row["prefix_tokens"] for row in result["mamba_states"]] == [4, 8, 10]
    assert result["mamba_states"][0]["session_ref"] == 1
    assert result["mamba_states"][1]["host_present"] is True
    assert result["probe_thread_cpu_ns"] >= 0
    json.dumps(result, allow_nan=False)
    assert arena[3].component_data[2].value is not None


@pytest.mark.parametrize("failure", [
    "detached", "cycle", "stale", "creation", "references", "too_deep",
])
def test_missing_or_untrusted_path_returns_unknown_without_partial_state(failure):
    req, cache, arena = fixture()
    limit = 256
    if failure == "detached":
        arena[1].parent = None
    elif failure == "cycle":
        arena[1].parent = arena[3]
    elif failure == "stale":
        original = cache.tree_core.node_by_id
        cache.tree_core.node_by_id = lambda node_id: NS(id=2) if node_id == 2 else original(node_id)
    elif failure == "creation":
        arena[1].creation_time = True
    elif failure == "references":
        arena[1].component_data[2].session_ref = -1
    else:
        limit = 2
    result = checkpoint_path_evidence(req, cache, max_nodes=limit)
    assert result["status"] == "unavailable"
    assert result["mamba_states"] == []


@pytest.mark.parametrize("enabled", [False, True])
def test_telemetry_records_path_only_for_opted_in_completed_requests(tmp_path, monkeypatch, enabled):
    import beliefkv.runtime.v0520_native_telemetry as module

    monkeypatch.setenv("BELIEFKV_CHECKPOINT_PATH_SHADOW", str(int(enabled)))
    probe = Mock(
        return_value={"status": "observed", "mamba_states": []},
    )
    monkeypatch.setattr(module, "checkpoint_path_evidence", probe)
    audit = NativeReactiveTelemetry(tmp_path / "server")
    req = NS(
        rid="request", output_ids=[1], finished=lambda: True,
        origin_input_ids=[1], session_id=None,
    )
    audit._active.add(req.rid)
    audit._queue_context_prefix = lambda *args, **kwargs: None
    audit._observe_targeted_pair = lambda *args, **kwargs: None
    audit._launched[1] = {
        "phase": "decode", "launch_mono": time.monotonic(), "batch_size": 1,
        "sample_id": "service", "mamba_forward_candidates": (),
        "request_samples": [{
            "request_id": req.rid, "workflow_id": "workflow", "invocation_id": "agent",
            "context_id": "context", "context_epoch": 0, "output_tokens_before": 0,
        }],
    }
    audit.on_completed(NS(forward_iter=1, reqs=[req]))
    audit.close()
    records = [json.loads(line) for line in (tmp_path / "server/runtime_events.sglang.jsonl").read_text().splitlines()]
    result = next(row for row in records if row["kind"] == "llm_result")
    assert ("checkpoint_path_evidence" in result["attributes"]) is enabled
    assert probe.call_count == int(enabled)
    status = json.loads((tmp_path / "server/native_telemetry_status.json").read_text())
    assert status["checkpoint_path_shadow"] is enabled


def test_invalid_shadow_switch_fails_before_creating_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("BELIEFKV_CHECKPOINT_PATH_SHADOW", "yes")
    with pytest.raises(ValueError):
        NativeReactiveTelemetry(tmp_path / "server")
    assert not (tmp_path / "server").exists()
