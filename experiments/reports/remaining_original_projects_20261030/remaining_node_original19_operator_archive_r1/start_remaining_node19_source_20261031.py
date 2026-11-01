"""Verify the complete source/data binding, then start only native19 collection."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-original19-source-operator-75414c7e")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "remaining_node_original19_gpu_entry_r1"
FROZEN = ROOT / "experiments/reports/remaining_original_projects_20261030/remaining_node_original19_source_r1"
CPU_PATH = REPORTS / "remaining_node_original19_bound_cpu_r2/evidence.json"
BINDING = REPORTS / "remaining_node_original19_source_r1/binding.json"
REFERENCE = OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"
NATIVE_REPORT = MAIN / "experiments/reports/layer_free_extent_20261029/native_cpu_r1/native_cpu.xml"
REGISTRATION = OPS / "workflow_remaining_node19_source_20261031_r1.registration.json"
RUN = MAIN / "experiments/raw/workflow_remaining_node19_source_20261031_node19r1"
PORT = 18950
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import FREE_EXTENT_TREE, engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts import run_causal_comparison as causal
from scripts import run_causal_source_capture as capture
from scripts.prepare_original_task_mix import source_integrity, validate_population
from scripts.qualify_free_page_h2d_fence import check_reservation
from scripts.qualify_predicted_tool_offload_cpu import cases
from scripts.train_workflow_work import write


def main():
    if os.getpriority(os.PRIO_PROCESS, 0) != 0 or HERE.exists() or REGISTRATION.exists() or RUN.exists():
        raise ValueError("keep GPU owners nice0 and preserve previous attempts")
    app = git_source_state(ROOT)
    if app["dirty"]:
        raise ValueError("source operator must be clean and committed")
    evidence = causal.load(CPU_PATH)
    report, sources = Path(evidence["report"]["path"]), Path(evidence["source_manifest"]["path"])
    if evidence["returncode"] or evidence["failed_error_skipped_cases"] or any(
        evidence.get(name) is not True for name in (
            "all_retained_literal_identities_present", "all_previous_source_paths_retained",
            "sources_unchanged_during_execution",
        )
    ):
        raise ValueError("full population-bound CPU qualification did not pass")
    if sha256_file(report) != evidence["report"]["sha256"] or (
        sha256_file(sources) != evidence["source_manifest"]["sha256"]
    ):
        raise ValueError("CPU evidence changed")
    identities, reported = cases(report)
    if len(identities) != evidence["unique_test_identities"] or (
        reported != evidence["junit_tests_including_subtests"]
    ):
        raise ValueError("CPU identity or suite/subtest count changed")
    causal.comparison.verify_cpu_snapshot(ROOT, causal.load(sources))
    for name, digest in evidence["external_inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("population-bound input changed: " + name)
    binding = causal.load(BINDING)
    for name, digest in binding["inputs_sha256"].items():
        if sha256_file(Path(name)) != digest:
            raise ValueError("original preparation changed: " + name)
    config_path, manifest_path = FROZEN / "native_qualified.json", FROZEN / "qualified_manifest.json"
    if str(config_path) != binding["configuration"] or (
        sha256_file(config_path) != binding["configuration_sha256"]
        or sha256_file(manifest_path) != binding["manifest_sha256"]
    ):
        raise ValueError("frozen configuration changed")
    config = causal.load(config_path)
    inputs = capture.declared_workload_inputs(manifest_path, require_tool_execution=True)
    population = capture.project_disjoint_workload(manifest_path, expected_workflows=19)
    with zipfile.ZipFile(population["archive"]) as archive:
        validate_population(population["workloads"], archive)
        integrity = {row["repo"]: source_integrity(row, archive) for row in population["workloads"]}
    if integrity != binding["original_source_integrity"]:
        raise ValueError("the original source/tests/context changed")
    engine = Path(evidence["environment"]["SGLANG_SOURCE_CHECKOUT"])
    if git_source_state(engine) != evidence["native_source"]:
        raise ValueError("CPU-qualified native source changed")
    native = git_source_state(engine, canonical_patch=engine_patch(ROOT, FREE_EXTENT_TREE))
    if native["canonical_patch"]["actual_tree"] != FREE_EXTENT_TREE or (
        native["canonical_patch"]["matches_worktree"] is not True
    ):
        raise ValueError("native tree/patch changed")
    settings = capture.environment(
        causal.load(REFERENCE), ROOT, engine, RUN, PORT,
        source_profile=config["source_profile"], source_workload=manifest_path,
        max_running_requests=24, sampling_profile="qwen3_coder",
        cuda_home=Path(config["common_environment"]["CUDA_HOME"]), experiment=config,
    )
    required = {
        "ROOT_COUNT": "19", "CLIENT_ROOT_CONCURRENCY": "12", "MAX_RUNNING_REQUESTS": "24",
        "CHILD_WORK_BUDGET_WARNING_CALLS": "4", "CHILD_WORK_BUDGET_TARGET_CALLS": "8",
        "ROOT_WORK_BUDGET_WARNING_CALLS": "8", "ROOT_WORK_BUDGET_TARGET_CALLS": "16",
        "MODEL_CACHE_LAYOUT": "full_only", "HICACHE_SIZE_GB": "200",
        "MODEL_CONTEXT_TOKENS": "262144", "SAMPLING_PROFILE": "qwen3_coder",
        "BELIEFKV_INTERNAL_COMPARISON_MODE": "native_matched", "BELIEFKV_AGENT_ADMISSION": "off",
        "BELIEFKV_ENABLE_TOOL_PREFETCH": "0", "BELIEFKV_ENABLE_PREDICTED_TOOL_OFFLOAD": "0",
        "BELIEFKV_ENABLE_PREPARE_HOST": "0", "BELIEFKV_WORKFLOW_SIZE_RESUME": "0",
        "BELIEFKV_LAYER_HANDOFF": "0", "BELIEFKV_ENABLE_FRONTIER_RESTORE": "0",
    }
    if any(settings.get(name) != value for name, value in required.items()):
        raise ValueError("native-only execution differs from the declaration")
    constructor = causal.preflight_runtime(settings, settings["MODEL_PATH"])
    if constructor != {"native_runtime_not_enabled": True}:
        raise ValueError("Full runtime leaked into native collection")
    authorization, resources = check_reservation(), causal.qualified.resources(PORT)
    if not resources["ready"] or Path(f"/tmp/bkv-causal-{PORT}.sock").exists():
        raise ValueError("GPU, Host, disk, port or event socket is unavailable")
    HERE.mkdir(parents=True, exist_ok=False)
    native_path = HERE / "native_sources.json"
    write(native_path, native)
    engine_proof = capture.engine_evidence(engine, NATIVE_REPORT, native_path)
    command = [
        sys.executable, str(ROOT / "scripts/run_causal_source_capture.py"),
        "--reference", str(REFERENCE), "--cpu-report", str(report), "--cpu-sources", str(sources),
        "--engine", str(engine), "--engine-report", str(NATIVE_REPORT),
        "--engine-sources", str(native_path), "--registration", str(REGISTRATION),
        "--run-root", str(RUN), "--port", str(PORT), "--source-profile", config["source_profile"],
        "--source-workload", str(manifest_path), "--max-running-requests", "24",
        "--sampling-profile", "qwen3_coder", "--cuda-home", settings["CUDA_HOME"],
        "--experiment-config", str(config_path),
    ]
    write(HERE / "preflight.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "application": app,
        "CPU_unique_cases": len(identities), "CPU_suite_tests_including_subtests": reported,
        "CPU_testcase_nodes": len(list(ET.parse(report).iter("testcase"))),
        "authorization": authorization, "resources": resources, "native_evidence": engine_proof,
        "native_runtime_isolation": constructor, "actual_environment": settings,
        "projects": 19, "client_root_concurrency": 12, "running_limit": 24,
        "original_files": sum(row["files"] for row in integrity.values()),
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in (
            CPU_PATH, report, sources, BINDING, config_path, manifest_path, REFERENCE,
            NATIVE_REPORT, native_path, Path(__file__), *inputs,
        )},
        "command": command, "capture_registered": False, "performance_accepted": False,
    })
    started = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    write(HERE / "registration_entry.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "returncode": started.returncode, "stdout": started.stdout, "stderr": started.stderr,
        "registration": str(REGISTRATION), "performance_accepted": False,
    })
    print(started.stdout, end="", flush=True)
    if started.returncode:
        print(started.stderr, file=sys.stderr, end="")
        raise RuntimeError("preserve the failed registration; no automatic retry")


if __name__ == "__main__":
    main()
