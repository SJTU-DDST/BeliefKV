import copy
import json
from pathlib import Path

import pytest

from beliefkv.experiments.agentix_qualification import QUALIFICATION_POLICY, load_qualification
from beliefkv.runtime.paper_baseline import create_adapter
from beliefkv.runtime.sglang_v0520_admission import _request_key
from tests.test_agentix_adapter import adapter
from tests.test_agentix_native_capacity import waiting
from tests.test_full_only_native_lifecycle import full_native


CONFIGURATION = (
    Path(__file__).resolve().parents[1]
    / "configs/baselines/agentix_native_qualification_20261025.json"
)


def test_native_qualification_is_explicit_and_cannot_supply_full_baseline_parameters():
    document, policy, serving = load_qualification(CONFIGURATION)
    assert document["purpose"] == "bounded_native_preemption_validation"
    assert document["max_requests"] == 4
    assert policy.mode == "atlas" and policy.decode_steps_per_schedule == 2
    assert serving == {
        "context_limit": 262144, "running_limit": 1, "decode_growth_tokens": 4,
    }


@pytest.mark.parametrize("change", (
    {"purpose": "full_baseline"},
    {"policy": "agentix_atlas_paper_native"},
    {"schema_version": True},
    {"max_requests": 0},
    {"max_requests": 17},
    {"max_requests": True},
    {"parameters": []},
    {"extra": "final_trace_output_lengths"},
))
def test_unbounded_or_mislabelled_qualification_documents_are_rejected(tmp_path, change):
    document = json.loads(CONFIGURATION.read_bytes())
    document.update(change)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="qualification"):
        load_qualification(path)


@pytest.mark.parametrize("field,value", (
    ("running_limit", 24),
    ("context_limit", 0),
    ("decode_growth_tokens", 1),
    ("mode", "plas"),
    ("quanta_ms", [0., 1., 2.]),
))
def test_qualification_requires_consistent_native_serving_and_policy_parameters(tmp_path, field, value):
    document = copy.deepcopy(json.loads(CONFIGURATION.read_bytes()))
    document["parameters"][field] = value
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_qualification(path)


def test_qualification_factory_requires_both_scope_enablement_and_pinned_configuration(adapter, monkeypatch):
    state = adapter
    with pytest.raises(ValueError, match="qualified runtime"):
        create_adapter(state.runtime, QUALIFICATION_POLICY)
    monkeypatch.setenv("BELIEFKV_AGENTIX_QUALIFICATION_ONLY", "1")
    with pytest.raises(ValueError, match="explicit configuration"):
        create_adapter(state.runtime, QUALIFICATION_POLICY)
    monkeypatch.setenv("BELIEFKV_AGENTIX_QUALIFICATION_CONFIG", str(CONFIGURATION))
    candidate = create_adapter(state.runtime, QUALIFICATION_POLICY)
    assert candidate.status()["qualification"]["max_requests"] == 4
    assert not candidate.status()["complete_baseline"]
    candidate.close()


def test_bounded_adapter_rejects_a_full_replay_without_creating_an_extra_call(adapter):
    state = adapter
    state.runtime.attach_native_scheduler(state.scheduler)
    state.paper.qualification = {
        "purpose": "bounded_native_preemption_validation", "max_requests": 2,
    }
    for identity in ("one", "two"):
        req = waiting(identity, (1, 2, 3))
        state.paper.registered(req, _request_key(req))
    third = waiting("three", (1, 2, 3))
    with pytest.raises(ValueError, match="cannot accept a full replay"):
        state.paper.registered(third, _request_key(third))
    assert state.paper.calls.counts["registered"] == 2
    assert len(state.paper.calls.requests) == 2
