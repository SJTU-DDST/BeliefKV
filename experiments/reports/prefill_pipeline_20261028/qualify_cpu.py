"""Run inherited CPU coverage and bind the demand pipeline to exact sources."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.run_causal_comparison import environment as launcher_environment, preflight_runtime


ADDED = (
    "tests/test_demand_prefill_pipeline.py",
    "tests/test_demand_prefill_pipeline_native.py",
    "tests/test_demand_prefill_pipeline_scheduler.py",
    "tests/test_demand_prefill_pipeline_configuration.py",
    "tests/test_demand_prefill_pipeline_engine.py",
    "tests/test_staged_native_restore_audit.py",
    "tests/test_paper_baseline_comparison.py",
    "tests/test_paper_baseline_runtime.py",
    "tests/test_demand_prefill_pipeline_service_audit.py",
    "tests/test_demand_prefill_pipeline_gpu_qualification.py",
    "tests/test_v0520_native_telemetry.py",
    "tests/test_demand_prefill_model_qualification.py",
)


def load(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def cases(path):
    root = ET.parse(path).getroot()
    identities, failed = set(), []
    for case in root.iter("testcase"):
        identity = case.get("classname"), case.get("name")
        identities.add(identity)
        if any(case.find(status) is not None for status in ("failure", "error", "skipped")):
            failed.append(identity)
    return identities, failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--previous", type=Path, default=HERE / "shared_r3_cpu.evidence.json",
    )
    args = parser.parse_args()
    if not args.name.replace("_", "").isalnum():
        raise ValueError("CPU evidence name must be a simple identifier")
    output = HERE / (args.name + ".evidence.json")
    manifest_path = HERE / (args.name + ".sources.json")
    report = HERE / (args.name + ".xml")
    if any(path.exists() for path in (output, manifest_path, report)):
        raise FileExistsError("preserve all earlier CPU attempts")
    prior_path = args.previous.resolve()
    prior = load(prior_path)
    inherited_manifest = Path(prior["source_manifest"]["path"])
    if sha256_file(inherited_manifest) != prior["source_manifest"]["sha256"]:
        raise ValueError("inherited CPU source manifest changed")
    paths = set(load(inherited_manifest)) | set(ADDED) | {
        "beliefkv/runtime/demand_prefill_pipeline.py",
        "beliefkv/experiments/demand_prefill_pipeline_engine.py",
        "scripts/audit_staged_native_restore.py", str(Path(__file__).relative_to(ROOT)),
        "scripts/audit_demand_prefill_pipeline.py", "scripts/qualify_demand_prefill_pipeline.py",
        "scripts/qualify_demand_prefill_model.py", "scripts/qualify_layer_handoff_model.py",
        "scripts/launch_qwen35_native_v0520.sh",
    }
    before = {name: sha256_file(ROOT / name) for name in sorted(paths)}
    engine = args.engine.resolve()
    native_before = git_source_state(engine)
    native_scheduler = engine / "python/sglang/srt/managers/scheduler.py"
    native_source = sha256_file(native_scheduler)
    command = [
        value for value in prior["command"] if not value.startswith("--junitxml=")
    ]
    command += [value for value in ADDED if value not in command]
    command += ["--junitxml=" + str(report)]
    environment = {
        **prior["environment"], "CUDA_VISIBLE_DEVICES": "99999",
        "SGLANG_SOURCE_CHECKOUT": str(engine), "PYTHONPATH": str(engine / "python"),
    }
    completed = subprocess.run(command, cwd=ROOT, env={**os.environ, **environment})
    if completed.returncode:
        raise RuntimeError(f"CPU qualification failed; retain {report}")
    current, failures = cases(report)
    original, original_failures = cases(Path(prior["report"]["path"]))
    if failures or original_failures or not original <= current:
        raise ValueError("CPU coverage is incomplete or contradicts inherited coverage")
    if before != {name: sha256_file(ROOT / name) for name in before} or (
        git_source_state(engine) != native_before or sha256_file(native_scheduler) != native_source
    ):
        raise ValueError("application or native sources changed during CPU qualification")
    configuration = ROOT / "configs/migration/predictive_sibling_service_capacity_20261028.json"
    common = {**load(configuration)["common_environment"], "DEMAND_PREFILL_PIPELINE": "1"}
    constructors = {}
    for mode in ("full", "thunderagent_acting_decay"):
        settings, _, _ = launcher_environment(
            {"arms": {"full": common}}, mode, ROOT, engine, HERE / "constructors" / mode,
            18600, ROOT, max_running_requests=int(common["MAX_RUNNING_REQUESTS"]),
        )
        constructors[mode] = preflight_runtime(settings, common["MODEL_PATH"])
        expected = mode == "full"
        actual = constructors[mode]["verified_configuration"]
        if actual["demand_prefill_pipeline_enabled"] is not expected or actual["enable_tool_prefetch"]:
            raise ValueError("pipeline or cancelled tool prefetch leaked across constructors")
    write(manifest_path, before)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual native CPU requests, admission, locks and scheduling hooks; copy/event stand-ins.",
        "command": command, "environment": environment, "native_engine": native_before,
        "native_scheduler_sha256": native_source,
        "passing_unique_case_identities": len(current),
        "retained_prior_unique_case_identities": len(original),
        "new_unique_case_identities": len(current - original),
        "previous_report": {"path": str(prior_path), "sha256": sha256_file(prior_path)},
        "report": {"path": str(report), "sha256": sha256_file(report)},
        "source_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "constructors": constructors,
        "configuration": {
            "base": str(configuration), "base_sha256": sha256_file(configuration),
            "explicit_change": {"DEMAND_PREFILL_PIPELINE": "1"},
            "formal_registration_created": False,
        },
        "gpu_correctness_verified": False, "performance_accepted": False,
        "limitations": [
            "No actual GPU DMA, layer waiting or model numerical equivalence is established.",
            "The source-bound scheduler fixture reaches batch construction, not GPU service.",
            "Staging ACK accounting does not establish first service or hidden transfer time.",
            "Exact engine packaging, native/model/position GPU qualifiers and complete matched work remain pending.",
        ],
    }
    write(output, result)
    print(json.dumps({
        name: result[name] for name in (
            "passing_unique_case_identities", "retained_prior_unique_case_identities",
            "new_unique_case_identities",
        )
    }, indent=2))


if __name__ == "__main__":
    main()
