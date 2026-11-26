from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import run_cross_harness_capture as runner


@pytest.fixture
def frozen(tmp_path):
    directory = tmp_path / "swebench"
    directory.mkdir()
    instances = list(runner.SWEBENCH_DIGESTS)
    source = directory / "pilot.json"
    source_repo = str(directory / "source_git")
    rows = [
        {
            "instance_id": task, "repo": "sympy/sympy", "base_commit": "a" * 40,
            "problem_statement": f"Original {task}\n",
        }
        for task in instances
    ]
    common = {"dataset": "princeton-nlp/SWE-bench_Verified", "dataset_revision": "pinned-revision"}
    source.write_text(json.dumps({**common, "source_repo": source_repo, "workloads": rows}))
    common.update(source_manifest=str(source), source_manifest_sha256=sha256(source.read_bytes()).hexdigest())
    band = {}
    for harness in runner.HARNESSES:
        manifest = directory / harness / "workloads.json"
        manifest.parent.mkdir()
        workloads = []
        for row in rows:
            prompt = manifest.parent / (row["instance_id"] + ".txt")
            prompt.write_text(row["problem_statement"])
            workloads.append({
                **row, "prompt_file": str(prompt), "prompt_sha256": sha256(prompt.read_bytes()).hexdigest(),
                "worktree": str(manifest.parent / row["instance_id"]), "source_repo": source_repo,
            })
        manifest.write_text(json.dumps({**common, "workloads": workloads}))
        band[harness] = {"path": str(manifest), "sha256": sha256(manifest.read_bytes()).hexdigest()}
    matrix = directory / "capture_matrix.json"
    matrix.write_text(json.dumps({**common, "instances": instances, "harness_manifests": band}))
    return matrix


def test_frozen_matrix_rejects_a_rehashed_task_change(frozen):
    cells = runner.cells("swebench", frozen.parent.parent)
    assert len(cells) == 12
    assert len({cell.workspace for cell in cells}) == 12
    assert len({runner.image_reference(cell) for cell in cells}) == 4
    matrix = json.loads(frozen.read_text())
    codex = matrix["harness_manifests"]["codex"]
    manifest = Path(codex["path"])
    modified = json.loads(manifest.read_text())
    modified["workloads"][0]["problem_statement"] = "Changed"
    manifest.write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        runner.cells("swebench", frozen.parent.parent)
    codex["sha256"] = sha256(manifest.read_bytes()).hexdigest()
    frozen.write_text(json.dumps(matrix))
    with pytest.raises(ValueError, match="differs in problem_statement"):
        runner.cells("swebench", frozen.parent.parent)


def test_time_namespace_binding_requires_same_clock():
    host = {
        "domain": "host", "namespace": "time:[11]", "monotonic_offset_ns": 0, "boot_id": "boot",
        "implementation": "clock_gettime(CLOCK_MONOTONIC)",
    }
    container = {**host, "domain": "container", "namespace": "time:[22]"}
    assert runner.bind_clock(host, container) == {"native": host, "container": container}
    for key, value in (("monotonic_offset_ns", 1), ("boot_id", "other-boot"), ("domain", None)):
        with pytest.raises(RuntimeError, match="clock differs"):
            runner.bind_clock(host, {**container, key: value})


def test_opencode_shell_restores_project_environment(tmp_path):
    cell = SimpleNamespace(dataset="locobench", workload={
        "repo": "locobench/python_api_microservice_medium_008",
        "sandbox_environment": {"PYTHONPATH": "/workspace/ledgerlink_gateway/ledgerlink/api/v1"},
    })
    settings = runner.tool_environment(cell, {
        "PATH": "/usr/bin", "PYTEST_ADDOPTS": "--asyncio-mode=auto",
    })
    runner.write_opencode_shell(tmp_path, settings)
    output = subprocess.check_output(
        [tmp_path / "bash", "-l", "-c", 'printf "%s\\n" "$PYTHONPATH" "$PATH" "$PYTEST_ADDOPTS"'],
        env={"PATH": "/bin"}, text=True,
    )
    assert output.splitlines() == [
        "/workspace:/workspace/ledgerlink_gateway",
        f"{runner.TESTBED_BIN}:/usr/bin", "--asyncio-mode=auto",
    ]


def test_preflight_stays_off_model_network(frozen, tmp_path, monkeypatch):
    load_cells = runner.cells
    monkeypatch.setattr(runner, "cells", lambda dataset: load_cells(dataset, frozen.parent.parent))
    monkeypatch.setattr(runner, "model_socket", lambda _: pytest.fail("preflight opened the model relay"))
    monkeypatch.setattr(runner, "verify_checkout", lambda _: tmp_path / "source.git")
    monkeypatch.setattr(runner, "inspect_image", lambda _: ("ref", "image-id", {"PATH": "/testbed"}))
    monkeypatch.setattr(runner, "check_cell", lambda *args: {"project_smoke": "ok"})
    report = tmp_path / "report.json"
    assert runner.main(["check", "--dataset", "swebench", "--report", str(report)]) == 0
    cells = json.loads(report.read_text())["cells"]
    opencode = [cell for cell in cells if cell["harness"] == "opencode"]
    assert len(cells) == 12 and all(cell["status"] == "ready" for cell in cells)
    assert all(Path(cell["raw_protocol"]).parent == Path(cell["events"]).parent for cell in opencode)


@pytest.mark.parametrize("harness", runner.HARNESSES)
def test_capture_delegates_to_pinned_entry_in_container(harness, frozen, tmp_path, monkeypatch):
    load_cells = runner.cells
    monkeypatch.setattr(runner, "cells", lambda dataset: load_cells(dataset, frozen.parent.parent))
    monkeypatch.setattr(runner, "verify_checkout", lambda _: tmp_path / "source.git")
    monkeypatch.setattr(runner, "inspect_image", lambda _: ("ref", "image-id", {"PATH": "/testbed"}))
    monkeypatch.setattr(runner, "clock_coordinates", lambda: {})

    @contextmanager
    def socket(url):
        assert url == "http://127.0.0.1:18000/v1"
        yield tmp_path / "socket"

    @contextmanager
    def container(cell, args, image, settings, git_dir, *, output, socket):
        assert cell.harness == harness
        assert output.is_dir() and socket.name == "socket"
        yield ["docker", "run", "image-id"], "control-python"

    monkeypatch.setattr(runner, "model_socket", socket)
    monkeypatch.setattr(runner, "container", container)
    calls = []
    monkeypatch.setattr(runner.subprocess, "call", lambda argv: calls.append(argv) or 0)
    assert runner.main([
        "capture", "--dataset", "swebench", "--harness", harness,
        "--instance", "sympy__sympy-13877", "--output", str(tmp_path / "out"),
        "--base-url", "http://127.0.0.1:18000/v1", "--model", "local-model",
        "--context-limit", "65536", "--max-output-tokens", "4096",
    ]) == 0
    argv, = calls
    assert argv[:10] == [
        "docker", "run", "image-id", runner.PROVIDER_RELAY, "18000", "control-python",
        runner.ENTRY, "_capture", harness, "{}",
    ]
    assert "4096" in argv
    if harness == "codex":
        assert {"--original-task", "--workspace-write", "--skip-gpu-monitor", "--instance"} <= set(argv)
    if harness == "opencode":
        assert {"--native-metadata", "--prompt", "--config"} <= set(argv)
    if harness == "openhands":
        assert {"capture", "--prompt-file", "--task-id"} <= set(argv)
