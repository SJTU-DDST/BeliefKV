import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import httpx
import pytest
from langchain_core.messages import HumanMessage

from beliefkv.experiments.deepagents_swebench import DeepAgentsExperimentConfig, _model
from scripts import run_causal_source_capture as capture
from scripts import run_deepagents_swebench as client
from scripts import run_position_replay_calibration as calibration
from tests.test_causal_source_capture import reference


@pytest.mark.parametrize("profile,temperature", (
    ("legacy_greedy", 0.0), ("qwen35_coding", 0.6),
))
def test_profile_reaches_real_chat_payload_without_changing_seed_or_stopping(
    tmp_path, profile, temperature,
):
    config = DeepAgentsExperimentConfig(
        mode="autonomous", base_url="http://127.0.0.1:18001/v1",
        model="Qwen3.5-35B-A3B", output_dir=tmp_path / "out",
        workload_manifest=tmp_path / "tasks.json", docker_image="fixture",
        sampling_seed=21, sampling_profile=profile,
    )
    adapter = NS(record_http_stream_diagnostic=lambda record: None)
    model = _model(config, adapter)
    try:
        payload = model._get_request_payload(
            [HumanMessage(content="Inspect this source.")],
            extra_body={
                **(model.extra_body or {}),
                "rid": "fixed-request",
                "beliefkv_metadata": {"context_id": "fixed-context"},
            },
        )
        assert payload["seed"] == 21
        assert payload["temperature"] == temperature
        assert payload["max_completion_tokens"] == config.max_completion_tokens
        assert payload["extra_body"]["rid"] == "fixed-request"
        assert payload["extra_body"]["beliefkv_metadata"] == {"context_id": "fixed-context"}
        wire = dict(payload)
        wire.update(wire.pop("extra_body"))
        request = httpx.Request(
            "POST", "http://127.0.0.1:18001/v1/chat/completions", json=wire,
        )
        for hook in model.http_client.event_hooks["request"]:
            hook(request)
        recorded = request.extensions["beliefkv_http_timing"]["generation_parameters"]
        assert recorded["seed"] == 21
        assert recorded["temperature"] == temperature
        if profile == "qwen35_coding":
            assert payload["top_p"] == 0.95
            assert payload["presence_penalty"] == 0.0
            assert {key: payload["extra_body"][key] for key in (
                "top_k", "min_p", "repetition_penalty",
            )} == {"top_k": 20, "min_p": 0.0, "repetition_penalty": 1.0}
            assert recorded == {
                "seed": 21, "temperature": 0.6, "top_p": 0.95, "top_k": 20,
                "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0,
            }
    finally:
        model.http_client.close()
        asyncio.run(model.http_async_client.aclose())


def test_unknown_sampling_profile_is_rejected_before_model_construction(tmp_path):
    with pytest.raises(ValueError, match="sampling_profile"):
        DeepAgentsExperimentConfig(
            mode="autonomous", base_url="http://localhost/v1", model="fixture",
            output_dir=tmp_path, workload_manifest=tmp_path / "tasks.json",
            docker_image="fixture", sampling_profile="unseeded",
        )


def test_cli_preserves_explicit_coding_profile_and_seed(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "client", "--mode", "autonomous", "--sampling-profile", "qwen35_coding",
        "--sampling-seed", "21",
    ])
    args = client.parse_args()
    assert args.sampling_profile == "qwen35_coding"
    assert args.sampling_seed == 21


def test_source_sampling_change_is_explicit_and_preserves_native_capacity_and_work(tmp_path):
    plan, checkout = reference(tmp_path)
    arguments = (plan, checkout, tmp_path / "engine", tmp_path / "run", 18570)
    old = capture.environment(*arguments)
    new = capture.environment(*arguments, sampling_profile="qwen35_coding")
    assert {key for key in new if new[key] != old.get(key)} == {"SAMPLING_PROFILE"}
    assert new["SAMPLING_PROFILE"] == "qwen35_coding"
    assert new["SAMPLING_SEED"] == new["SERVER_RANDOM_SEED"] == "21"
    assert new["NATIVE_POLICY_BASELINE"] == "1"
    assert "SAMPLING_PROFILE" not in plan["arms"]["native_matched"]
    with pytest.raises(ValueError, match="sampling profile"):
        capture.environment(*arguments, sampling_profile="unknown")


@pytest.mark.parametrize("source_profile,control_profile", (
    ("qwen35_coding", "legacy_greedy"), ("legacy_greedy", "qwen35_coding"),
))
def test_control_registration_rejects_sampling_profile_mismatch_before_gpu(
    tmp_path, monkeypatch, source_profile, control_profile,
):
    manifest = tmp_path / "tasks.json"
    manifest.write_text("{}")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "source_profile": "nested_oneshot",
        "environment": {
            "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "24",
            "SAMPLING_PROFILE": source_profile, "WORKLOAD_MANIFEST": str(manifest),
        },
    }))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "source_profile": "nested_oneshot", "order": ["full", "scheduler_only"],
        "common_environment": {
            "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "24",
            "SAMPLING_PROFILE": control_profile,
            "FANOUT_PROFILE": "native_in_graph_nested_2to4",
            "BELIEFKV_SUBAGENT_RETENTION": "off", "WORKLOAD_MANIFEST": str(manifest),
        },
    }))
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps({"arms": {"full": {}}}))
    args = NS(
        registration=tmp_path / "queue.json", run_root=tmp_path / "run",
        comparison_registration=tmp_path / "comparison.json",
        comparison_root=tmp_path / "comparison", source_registration=source,
        reference=reference_path, experiment_config=spec,
    )
    monkeypatch.setattr(calibration.capture, "project_disjoint_workload", lambda *a, **kw: {})
    monkeypatch.setattr(calibration.comparison, "verify_sources", lambda plan: None)
    monkeypatch.setattr(calibration.fresh, "check_hold", lambda *a, **kw: None)
    monkeypatch.setattr(calibration.fresh, "verify", lambda plan: None)
    with pytest.raises(ValueError, match="predeclared source"):
        calibration.register(args)
    assert not args.registration.exists()


def test_declared_coding_experiment_keeps_project_disjoint_work_and_system_policy():
    root = Path(__file__).resolve().parents[1]
    old = json.loads((root / (
        "configs/migration/qwen35_locobench_oneshot_36project_running24_20261024.json"
    )).read_text())
    new = json.loads((root / (
        "configs/migration/qwen35_locobench_oneshot_36project_running24_coding_20261024.json"
    )).read_text())
    common = dict(new["common_environment"])
    assert common.pop("SAMPLING_PROFILE") == "qwen35_coding"
    assert common == old["common_environment"]
    assert new["order"] == old["order"] == ["full", "scheduler_only"]
