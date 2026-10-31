"""Retain prior CPU cases and bind the final incremental-source inputs."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from beliefkv.experiments.composite_workload import KIND, load_composed_source
from scripts.qualify_adaptive_tool_policy_cpu import source_manifest
from scripts.qualify_predicted_tool_offload_cpu import cases
from scripts.run_causal_source_capture import declared_workload_inputs
from scripts.train_workflow_work import write


def sources(retained_paths=()):
    result = source_manifest()
    for name in retained_paths:
        path = ROOT / name
        if not path.is_file():
            raise ValueError("a previously bound source input is missing: " + name)
        result[name] = sha256_file(path)
    for path in (ROOT / "configs/docker").glob("*.Dockerfile"):
        result[str(path.relative_to(ROOT))] = sha256_file(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-evidence", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--additional-tests", nargs="*", default=[])
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError("preserve previous CPU qualifications")
    previous = json.loads(args.previous_evidence.read_bytes())
    report = Path(previous["report"]["path"])
    if sha256_file(report) != previous["report"]["sha256"]:
        raise ValueError("retained CPU evidence changed")
    retained, _ = cases(report)
    prior_sources = Path(previous["source_manifest"]["path"])
    if sha256_file(prior_sources) != previous["source_manifest"]["sha256"]:
        raise ValueError("retained source manifest changed")
    retained_paths = json.loads(prior_sources.read_bytes()).keys()
    before, native = sources(retained_paths), git_source_state(args.engine)
    configuration = json.loads(args.configuration.read_bytes())
    manifest = ROOT / configuration["common_environment"]["WORKLOAD_MANIFEST"]
    composition = manifest.parent / "composite_source.json"
    source_kind = "natural_original_source"
    if composition.is_file():
        load_composed_source(manifest.parent)
        declaration = json.loads(composition.read_bytes())
        inputs = [
            args.configuration, composition, manifest,
            *(manifest.parent / name for name in declaration["artifacts_sha256"]),
            *(Path(name) for name in declaration["original_inputs_sha256"]),
        ]
        source_kind = KIND
    else:
        inputs = [
            args.configuration, *declared_workload_inputs(manifest, require_tool_execution=True),
        ]
    external = {str(path.resolve()): sha256_file(path) for path in inputs}
    here = args.output_dir.resolve()
    here.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable, *[
            value for value in previous["command"][1:]
            if not value.startswith("--junitxml=")
        ],
    ]
    if "tests/test_original_node_projects.py" not in command:
        command.append("tests/test_original_node_projects.py")
    for name in args.additional_tests:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("tests",) or not (
            path.suffix == ".py" and (ROOT / path).is_file()
        ):
            raise ValueError("additional qualifications must name existing repository test modules")
        if name not in command:
            command.append(name)
    command.append("--junitxml=" + str(here / "bound_cpu.xml"))
    env = {
        **os.environ, **previous["environment"],
        "SGLANG_SOURCE_CHECKOUT": str(args.engine.resolve()),
        "PYTHONPATH": str(args.engine.resolve() / "python"),
        "CUDA_VISIBLE_DEVICES": "99999", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    }
    status = subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    current, reported = cases(here / "bound_cpu.xml") if status == 0 else (set(), None)
    unchanged = (
        before == sources(retained_paths) and native == git_source_state(args.engine)
        and external == {str(path.resolve()): sha256_file(path) for path in inputs}
    )
    write(here / "sources.json", before)
    write(here / "evidence.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "command": command,
        "environment": {name: env[name] for name in (
            "CUDA_VISIBLE_DEVICES", "PYTHONHASHSEED", "SGLANG_SOURCE_CHECKOUT", "PYTHONPATH",
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "BELIEFKV_TEST_SANDBOX_DOCKER",
            "BELIEFKV_TEST_SANDBOX_IMAGE", "BELIEFKV_CONTEXT_TOKENIZER_TEST",
        )},
        "returncode": status, "unique_test_identities": len(current),
        "junit_tests_including_subtests": reported,
        "all_retained_literal_identities_present": retained <= current,
        "all_previous_source_paths_retained": set(retained_paths) <= before.keys(),
        "missing_identities": [list(value) for value in sorted(retained - current)],
        "sources_unchanged_during_execution": unchanged, "native_source": native,
        "external_inputs_sha256": external,
        "source_manifest": {
            "path": str(here / "sources.json"), "sha256": sha256_file(here / "sources.json"),
        },
        "report": {
            "path": str(here / "bound_cpu.xml"), "sha256": sha256_file(here / "bound_cpu.xml"),
        },
        "prior_evidence_sha256": {str(args.previous_evidence.resolve()): sha256_file(args.previous_evidence)},
        "GPU_experiment_started": False, "performance_accepted": False,
        "source_kind": source_kind,
        "composition_origins_require_complete_registration_proofs_before_GPU": source_kind == KIND,
    })
    if status or not unchanged or not retained <= current:
        raise SystemExit("incremental source CPU qualification failed; preserve its evidence")
    print(json.dumps({"passing": len(current), "retained": len(retained),
                      "sources": len(before), "GPU_action": False}))


if __name__ == "__main__":
    main()
