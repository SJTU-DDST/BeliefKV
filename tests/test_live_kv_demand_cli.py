from array import array
from contextlib import nullcontext
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RuntimeEventKind as Kind
from scripts import audit_live_kv_demand as audit


def test_periodic_probe_and_latest_tags_survive_processing_runtime_events(monkeypatch, tmp_path):
    source, run = tmp_path / "source", tmp_path / "run"
    (source / "server").mkdir(parents=True)
    (source / "server/native_capacity_census.json").write_text(json.dumps({
        "capacity": {"device_full_tokens": 2, "host_full_tokens": 4},
    }))
    actual_capacity = {"device_full_tokens": 2, "host_full_tokens": 8}
    (run / "full/server").mkdir(parents=True)
    (run / "full/server/native_capacity_census.json").write_text(json.dumps({
        "capacity": actual_capacity,
    }))
    registration = tmp_path / "registration.json"
    registration.write_text(json.dumps({
        "source_arm": str(source), "run_root": str(run),
        "expected_replay_capacity": actual_capacity,
    }))
    identity = {"workflow_id": "w", "invocation_id": "i", "context_id": "c"}
    events = [
        {**identity, "ts_ms": when, "kind": kind, "attributes": {"request_id": "r"}}
        for when, kind in (
            (0., "workflow_start"), (.5, "invocation_create"),
            (1., "llm_submit"), (2., "llm_result"), (180000., "tool_start"),
        )
    ]
    source_event = NS(**identity, kind=Kind.LLM_RESULT, attributes={"request_id": "r"})
    graph = NS(nodes=(NS(node_id="result", event=source_event),),
               dependency_graph=lambda: {"result": ()})
    monkeypatch.setattr(audit, "source_plan", lambda _path: (graph, {}, {}))
    value = NS(
        prompt_tokens=array("I", [1, 2]), output_tokens=array("I", [3]),
        extra_key=None, cache_salt=None,
    )
    monkeypatch.setattr(audit, "CapturedTokenCorpus", lambda *_args: nullcontext(NS(get=lambda _r: value)))

    class Records:
        prefixes = {}

        def __call__(self, path):
            if path.name == "runtime_events.replay.jsonl":
                return events
            if path.name == "admission_opportunities.jsonl":
                return [{"event": "admission_runtime_state", "ts_ms": 1000., "monotonic_ms": 0.}]
            return [{
                "event": "context_prefix_reuse_probe", "ts_ms": 121000.,
                "request_id": "r", "previously_served_prefix_recompute_proxy_tokens": 2,
            }]

    monkeypatch.setattr(audit, "SealedRecords", Records)
    output = {}
    monkeypatch.setattr(audit, "write", lambda _path, result: output.update(result))
    monkeypatch.setattr(audit.sys, "argv", [
        "audit", "--registration", str(registration), "--output", str(tmp_path / "demand.json"),
    ])
    audit.main()
    assert {row["observation_kind"] for row in output["observations"]} == {
        "periodic", "prefix_loss_probe", "latest_event",
    }
    periodic = [row for row in output["observations"] if row["observation_kind"] == "periodic"]
    assert [row["arrival_offset_s"] for row in periodic] == [0., 60., 120., 180.]
    assert output["regular_sample_interval_s"] == 60
    assert output["combined_FULL_capacity_tokens"] == 10
    assert output["capacity_evidence"]["capacity"] == actual_capacity
    assert output["capacity_evidence"]["source_capture_capacity_not_used"] is True
    latest = output["observations"][-1]
    assert latest["latest_served_union_fraction_of_combined_ceiling"] == .3


def test_replay_capacity_requires_the_actual_census_even_with_registered_capacity(tmp_path):
    with pytest.raises(FileNotFoundError):
        audit.replay_capacity({
            "expected_replay_capacity": {"device_full_tokens": 2, "host_full_tokens": 8},
        }, tmp_path)


def test_replay_capacity_rejects_a_mismatched_registration(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server/native_capacity_census.json").write_text(json.dumps({
        "capacity": {"device_full_tokens": 2, "host_full_tokens": 4},
    }))
    with pytest.raises(ValueError, match="differs from its registered capacity"):
        audit.replay_capacity({
            "expected_replay_capacity": {"device_full_tokens": 2, "host_full_tokens": 8},
        }, tmp_path)


@pytest.mark.parametrize("device,host", [(0, 4), (True, 4), (2, -1), (2, 4.0)])
def test_replay_capacity_rejects_invalid_token_counts(tmp_path, device, host):
    (tmp_path / "server").mkdir()
    capacity = {"device_full_tokens": device, "host_full_tokens": host}
    (tmp_path / "server/native_capacity_census.json").write_text(json.dumps({
        "capacity": capacity,
    }))
    with pytest.raises(ValueError, match="valid token counts"):
        audit.replay_capacity({"expected_replay_capacity": capacity}, tmp_path)
