import json

import pytest

from beliefkv.experiments.native_engine_variant import COMMON_TREE
from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_agentx_baseline as paper
from scripts import run_causal_comparison as causal
from tests.test_causal_comparison import reference


@pytest.mark.parametrize("label", ("full", "native_matched", "thunderagent_acting_decay", "join_priority_only"))
def test_non_prefix_policies_do_not_start_paper_components(label, tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("unrelated policy started a paper component")

    monkeypatch.setattr(paper, "qualify_native_prefix", unexpected)
    monkeypatch.setattr(paper, "qualify_native_tokencake", unexpected)
    assert causal.qualify_paper_components({}, label, tmp_path, pulse=unexpected, owned=[]) is None


@pytest.mark.parametrize("label", ("kvflow_observed_graph", "tokencake_paper_observed"))
def test_paper_components_use_current_native_patch_and_finish_in_order(label, tmp_path, monkeypatch):
    patch = tmp_path / "native.patch"
    patch.write_text("reviewed-native-bytes")
    monkeypatch.setattr(causal, "engine_patch", lambda *_args: patch)
    plan = {"engine_tree": COMMON_TREE, "engine_checkout": "/native", "reference_environment": {}}
    calls, owned = [], []

    def prefix(registration, run, *, pulse, owned):
        assert registration["native_patch_sha256"] == sha256_file(patch)
        assert registration["engine_checkout"] == plan["engine_checkout"] and run == tmp_path
        calls.append("prefix")
        owned.append({"pid": 10})
        pulse()
        return {"report": "prefix-report", "sha256": "prefix-sha"}

    def tokencake(registration, run, *, pulse, owned):
        assert calls == ["prefix"]
        calls.append("tokencake")
        owned.append({"pid": 20})
        pulse()
        return {"report": "lifecycle-report", "sha256": "lifecycle-sha"}

    monkeypatch.setattr(paper, "qualify_native_prefix", prefix)
    monkeypatch.setattr(paper, "qualify_native_tokencake", tokencake)
    pulses = []
    result = causal.qualify_paper_components(
        plan, label, tmp_path, pulse=lambda: pulses.append(tuple(owned)), owned=owned,
    )
    assert result["prefix"] == {"report": "prefix-report", "sha256": "prefix-sha"}
    assert calls == (["prefix"] if label == "kvflow_observed_graph" else ["prefix", "tokencake"])
    assert len(pulses) == len(calls)
    if label == "tokencake_paper_observed":
        assert result["tokencake_lifecycle"]["report"] == "lifecycle-report"
    assert "native_patch_sha256" not in plan


@pytest.mark.parametrize("label", ("kvflow_observed_graph", "tokencake_paper_observed"))
def test_unqualified_native_tree_is_rejected_before_any_component(label, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(paper, "qualify_native_prefix", lambda *_args, **_kwargs: calls.append(1))
    with pytest.raises(ValueError, match="common native engine"):
        causal.qualify_paper_components(
            {"engine_tree": "other"}, label, tmp_path, pulse=lambda: None, owned=[],
        )
    assert not calls


@pytest.mark.parametrize("label", ("kvflow_observed_graph", "tokencake_paper_observed"))
def test_prefix_failure_prevents_following_components(label, tmp_path, monkeypatch):
    patch = tmp_path / "native.patch"
    patch.write_text("native")
    monkeypatch.setattr(causal, "engine_patch", lambda *_args: patch)

    def refused(*_args, **_kwargs):
        raise ValueError("exact GPU byte check failed")

    monkeypatch.setattr(paper, "qualify_native_prefix", refused)
    monkeypatch.setattr(
        paper, "qualify_native_tokencake",
        lambda *_args, **_kwargs: pytest.fail("lifecycle ran after failed prefix"),
    )
    with pytest.raises(ValueError, match="exact GPU byte check"):
        causal.qualify_paper_components(
            {"engine_tree": COMMON_TREE}, label, tmp_path, pulse=lambda: None, owned=[],
        )


def test_watch_checks_components_before_model_and_preserves_all_owned_children(tmp_path, monkeypatch):
    registration = tmp_path / "comparison.json"
    run = tmp_path / "run"
    registration.write_text(json.dumps({
        "run_root": str(run), "order": ["kvflow_observed_graph"],
        "engine_tree": COMMON_TREE, "port": 18700,
        "reference_environment": {}, "engine_checkout": "/native",
    }))
    cleaned, observed = [], []
    monkeypatch.setattr(causal.comparison, "verify_sources", lambda _plan: None)
    monkeypatch.setattr(causal, "predecessor_ready", lambda _plan: True)
    monkeypatch.setattr(causal, "predecessor_work", lambda _plan: None)
    monkeypatch.setattr(causal, "wait_for_resources", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(causal.fresh, "check_hold", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(causal.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(causal.reservation, "process", lambda _pid: {"pid": 1})
    monkeypatch.setattr(causal, "owned_processes", lambda rows: list(rows))
    monkeypatch.setattr(causal.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        causal, "environment",
        lambda *_args, **_kwargs: pytest.fail("model setup ran before component qualification"),
    )
    monkeypatch.setattr(
        causal, "cleanup", lambda _arm, owned, _reason: cleaned.extend(owned),
    )

    def components(_plan, label, arm, *, pulse, owned):
        assert label == "kvflow_observed_graph" and arm == run / label
        owned.append({"pid": 22})
        pulse()
        observed.append(json.loads(registration.with_suffix(".state.json").read_bytes()))
        owned.append({"pid": 23})
        pulse()
        observed.append(json.loads(registration.with_suffix(".state.json").read_bytes()))
        raise ValueError("component refused before model startup")

    monkeypatch.setattr(causal, "qualify_paper_components", components, raising=False)
    causal.watch(registration)
    state = json.loads(registration.with_suffix(".state.json").read_bytes())
    assert state["status"] == "stopped_for_investigation"
    assert "component refused before model startup" in state["error"]
    assert observed[0]["status"] == "qualifying_components_kvflow_observed_graph"
    assert observed[1]["owned"] == [{"pid": 22}, {"pid": 23}]
    assert cleaned == [{"pid": 22}, {"pid": 23}]
    assert not state["completed_arms"]


def test_locobench_kvflow_declaration_uses_the_isolated_current_runtime(reference, tmp_path):
    spec = json.loads((causal.ROOT / "configs/baselines/kvflow_locobench_v230_v311_20261028.json").read_bytes())
    configured = causal.experiment_reference(reference, spec)
    configured["arms"]["full"]["DEVICE_FULL_TOKENS"] = "805198"
    settings, _, _ = causal.environment(
        configured, "kvflow_observed_graph", tmp_path, tmp_path, tmp_path, 18700,
        tmp_path, sampling_mode="position", max_running_requests=24,
    )
    for name in (
        "BELIEFKV_ENABLE_TOOL_PREFETCH", "BELIEFKV_ENABLE_PREPARE_HOST",
        "BELIEFKV_ENABLE_RESIDENT_FIRST", "BELIEFKV_ENABLE_JOIN_PARENT_PRIORITY",
        "BELIEFKV_ENABLE_TOOL_CONTINUITY", "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD",
        "BELIEFKV_DEMAND_PREFILL_PIPELINE", "BELIEFKV_LAYER_HANDOFF",
    ):
        assert settings[name] == "0"
    assert settings["BELIEFKV_AGENT_ADMISSION"] == "off"
    assert settings["MAX_RUNNING_REQUESTS"] == "24" and settings["ROOT_COUNT"] == "18"
    assert settings["BELIEFKV_PAPER_BASELINE"] == "kvflow_paper_observed_graph"
    assert causal.preflight_runtime(settings, str(tmp_path))["runtime_configuration_verified"]
