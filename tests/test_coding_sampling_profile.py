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
    ("legacy_greedy", 0.0), ("qwen35_coding", 0.6), ("qwen3_coder", 0.7),
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
        elif profile == "qwen3_coder":
            assert recorded == {
                "seed": 21, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.05,
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


@pytest.mark.parametrize("source_profile,control_profile,cuda_mismatch", (
    ("qwen35_coding", "legacy_greedy", False),
    ("legacy_greedy", "qwen35_coding", False),
    ("qwen35_coding", "qwen35_coding", True),
), ids=(
    "qwen35_coding-legacy_greedy", "legacy_greedy-qwen35_coding",
    "matched-sampling-different-toolchain",
))
def test_control_registration_rejects_sampling_profile_mismatch_before_gpu(
    tmp_path, monkeypatch, source_profile, control_profile, cuda_mismatch,
):
    manifest = tmp_path / "tasks.json"
    manifest.write_text("{}")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({
        "source_profile": "nested_oneshot",
        "environment": {
            "ROOT_COUNT": "36", "MAX_RUNNING_REQUESTS": "24",
            "SAMPLING_PROFILE": source_profile, "WORKLOAD_MANIFEST": str(manifest),
            **({"CUDA_HOME": "/source/cuda"} if cuda_mismatch else {}),
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
            **({"CUDA_HOME": "/other/cuda"} if cuda_mismatch else {}),
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
    assert common.pop("CUDA_HOME") == (
        "/home/longhao/experiment/toolchains/cuda130_sampling_20261024/nvidia/cu13"
    )
    assert common == old["common_environment"]
    assert new["order"] == old["order"] == ["full", "scheduler_only"]


@pytest.mark.parametrize("compiler,runtime,accepted", (
    ("13.0", 13000, True), ("13.4", 13000, False), ("13.0", 13040, False),
))
def test_capture_toolchain_checks_actual_compiler_and_headers_before_gpu(
    tmp_path, monkeypatch, compiler, runtime, accepted,
):
    names = (
        "bin/nvcc", "bin/ptxas", "nvvm/bin/cicc", "nvvm/libdevice/libdevice.10.bc",
        "include/cuda_runtime_api.h", "include/curand.h", "include/curand_kernel.h",
        "include/nv/target", "include/cccl/cub/version.cuh",
        "lib/libcudart.so.13", "lib64/libcudart.so",
    )
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#define CUDART_VERSION {runtime}\n" if name.endswith(
            "cuda_runtime_api.h",
        ) else "fixture")
    monkeypatch.setattr(
        capture.qualified, "command", lambda args: f"Cuda compilation tools, release {compiler}",
    )
    if accepted:
        evidence = capture.cuda_toolchain_evidence(tmp_path)
        assert evidence["runtime_version"] == runtime
        assert len(evidence["inputs"]) == len(names)
        assert evidence["home"] == str(tmp_path.resolve())
        (tmp_path / "include/curand.h").unlink()
        with pytest.raises(ValueError, match="incomplete"):
            capture.cuda_toolchain_evidence(tmp_path)
    else:
        with pytest.raises(ValueError, match="versions must match"):
            capture.cuda_toolchain_evidence(tmp_path)


def test_sampler_preflight_is_once_only_and_rechecks_gpu_after_the_probe(tmp_path, monkeypatch):
    output = tmp_path / "sampling.json"
    plan = {
        "port": 18570, "wait_for_gpu": True, "cuda_toolchain": {"home": "/matched/cuda"},
        "engine_checkout": "/matched/sglang",
        "sampling_preflight_path": str(output),
    }
    calls = []
    available = iter((True, False, True))
    monkeypatch.setattr(capture, "wait_for_resources", lambda *a: None)
    monkeypatch.setattr(capture, "queue_launch_allowed", lambda plan: True)
    monkeypatch.setattr(capture.comparison, "verify_sources", lambda plan: None)
    monkeypatch.setattr(capture.qualified, "resources", lambda port: {"ready": next(available)})

    def run(command, **kwargs):
        if str(command[1]).endswith("preflight_cuda_sampling.py"):
            calls.append("sampling")
            assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "0"
            assert kwargs["env"]["CUDA_HOME"] == "/matched/cuda"
            assert kwargs["env"]["PYTHONPATH"].startswith("/matched/sglang/python")
            output.write_text(json.dumps({
                "gpu_sampling_verified": True, "cuda_home": "/matched/cuda",
                "native_kernel_verified": True,
            }))
        else:
            calls.append("host")

    monkeypatch.setattr(capture.subprocess, "run", run)
    state = {}
    capture.prepare_capture(plan, state, tmp_path / "state.json", lambda: False)
    assert calls == ["host", "sampling", "host"]
    assert state["sampling_kernel_preflight"]["gpu_sampling_verified"] is True


def test_failed_sampler_preflight_preserves_compiler_diagnostics_before_model_start(
    tmp_path, monkeypatch,
):
    output = tmp_path / "sampling.json"
    plan = {
        "port": 18570, "wait_for_gpu": True, "cuda_toolchain": {"home": "/matched/cuda"},
        "engine_checkout": "/matched/sglang",
        "sampling_preflight_path": str(output),
    }
    monkeypatch.setattr(capture, "wait_for_resources", lambda *a: None)
    monkeypatch.setattr(capture, "queue_launch_allowed", lambda plan: True)
    monkeypatch.setattr(capture.comparison, "verify_sources", lambda plan: None)
    monkeypatch.setattr(capture.qualified, "resources", lambda port: {"ready": True})

    def run(command, **kwargs):
        if str(command[1]).endswith("preflight_cuda_sampling.py"):
            raise capture.subprocess.CalledProcessError(
                1, command, output="compiler output", stderr="header/compiler mismatch",
            )

    monkeypatch.setattr(capture.subprocess, "run", run)
    with pytest.raises(ValueError, match="sampler preflight failed"):
        capture.prepare_capture(plan, {}, tmp_path / "state.json", lambda: False)
    assert not output.exists()
    assert json.loads(output.with_suffix(".error.json").read_text())["stderr"] == (
        "header/compiler mismatch"
    )


def test_sampling_success_alone_does_not_qualify_native_kernel_startup(tmp_path, monkeypatch):
    output = tmp_path / "sampling.json"
    plan = {
        "port": 18570, "wait_for_gpu": True, "cuda_toolchain": {"home": "/matched/cuda"},
        "engine_checkout": "/matched/sglang", "sampling_preflight_path": str(output),
    }
    monkeypatch.setattr(capture, "wait_for_resources", lambda *a: None)
    monkeypatch.setattr(capture, "queue_launch_allowed", lambda plan: True)
    monkeypatch.setattr(capture.comparison, "verify_sources", lambda plan: None)
    monkeypatch.setattr(capture.qualified, "resources", lambda port: {"ready": True})

    def run(command, **kwargs):
        if str(command[1]).endswith("preflight_cuda_sampling.py"):
            output.write_text(json.dumps({
                "gpu_sampling_verified": True, "cuda_home": "/matched/cuda",
            }))

    monkeypatch.setattr(capture.subprocess, "run", run)
    with pytest.raises(ValueError, match="preflight did not qualify"):
        capture.prepare_capture(plan, {}, tmp_path / "state.json", lambda: False)
