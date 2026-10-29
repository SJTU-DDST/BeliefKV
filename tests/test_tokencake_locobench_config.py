import json
from pathlib import Path

from beliefkv.experiments.paper_baselines import (
    TOKENCAKE_PARAMETERS, environment, tokencake_parameters, validate_state,
    validate_tokencake_parameters,
)
from beliefkv.runtime.sglang_v0520_runtime import NativeAdmissionRuntime
from scripts import run_causal_comparison as causal


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/baselines/tokencake_locobench_v230_v311_20261028.json"


def test_formal_tokencake_preserves_common_work_and_independent_paper_parameters():
    document = json.loads(CONFIG.read_bytes())
    kvflow = json.loads((ROOT / "configs/baselines/kvflow_locobench_v230_v311_20261028.json").read_bytes())
    paper = json.loads((ROOT / document["paper_configuration"]).read_bytes())
    assert document["order"] == ["tokencake_paper_observed"]
    assert document["tool_timing_overlay"] == kvflow["tool_timing_overlay"]
    assert document["native_reference"] == {"repeat_full_native_replay": False}
    assert {
        name: value for name, value in document["common_environment"].items()
        if name not in TOKENCAKE_PARAMETERS.values()
    } == kvflow["common_environment"]
    assert tokencake_parameters(document["common_environment"]) == paper["parameters"]


def test_real_tokencake_constructor_applies_declared_parameters_without_full_policy(
    tmp_path, monkeypatch,
):
    common = json.loads(CONFIG.read_bytes())["common_environment"]
    for name, value in environment(common, "tokencake_paper_observed").items():
        monkeypatch.setenv(name, value)
    runtime = NativeAdmissionRuntime(
        event_socket_path=str(tmp_path / "events.sock"),
        model_path=common["MODEL_PATH"], opportunity_dir=tmp_path,
    )
    try:
        validate_tokencake_parameters(
            runtime._paper_baseline.status(), tokencake_parameters(common),
        )
        assert runtime._agent_admission is None
        assert runtime.enable_tool_prefetch is False
        assert runtime.demand_prefill_pipeline_enabled is False
    finally:
        runtime.close()
    state = causal.qualified.last_runtime_state(tmp_path / "admission_opportunities.jsonl")
    validate_state("tokencake_paper_observed", state)
