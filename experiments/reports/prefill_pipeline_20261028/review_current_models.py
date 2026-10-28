"""Keep deterministic I/O qualification separate from ordinary-math diagnostics."""

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
APPLICATION = ROOT.parent / ".worktrees/beliefkv-demand-formal-01510ed5-20261028"
sys.path.insert(0, str(APPLICATION))

from beliefkv.experiments.native_engine_variant import DEMAND_PREFILL_PIPELINE_TREE
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from scripts.qualify_demand_prefill_model import (
    LABELS, REFERENCE_INITIALS, generation_document, output_diagnostics, validate_model_parameters,
    compare_outputs, verify_progress,
)
from scripts.qualify_demand_prefill_pipeline import validate_scenarios
from scripts.qualify_full_native_model import response_record
from scripts.qualify_layer_handoff_model import records
from scripts.audit_staged_native_restore import inspect, service_evidence


def load(path, seals):
    before = sha256_file(path)
    value = json.loads(path.read_bytes())
    if sha256_file(path) != before:
        raise ValueError("model qualification input changed while reading")
    seals[str(path)] = before
    return value


def responses(directory, seals, *, warm):
    bodies, rows = {}, {}
    for name in (*LABELS, *(REFERENCE_INITIALS if warm else ())):
        bodies[name] = load(directory / f"request_{name}.json", seals)
        rows[name] = response_record(
            load(directory / f"response_{name}.json", seals),
            request=bodies[name]["rid"], prompt_tokens=len(bodies[name]["input_ids"]),
            document=generation_document(name),
        )
    return bodies, rows


def exited(directory, report, seals):
    if load(directory / "owned_process_cleanup.json", seals)["remaining_processes"]:
        raise ValueError("owned model processes remain")
    identity = report["server"]
    if Path(f"/proc/{identity['pid']}").exists():
        raise ValueError("model server has not actually exited")


def runtime_projection(old, new):
    path = "beliefkv/runtime/sglang_v0520_runtime.py"
    versions = [
        ast.parse(subprocess.check_output(
            ["git", "show", revision + ":" + path], cwd=ROOT, text=True,
        )) for revision in (old, new)
    ]
    removed = 0
    for node in ast.walk(versions[1]):
        if not isinstance(node, ast.Dict):
            continue
        indices = [
            index for index, key in enumerate(node.keys)
            if isinstance(key, ast.Constant) and key.value == "demand_prefill_pipeline_progress_probe"
        ]
        for index in reversed(indices):
            expected = ast.parse("pipeline is not None and pipeline.progress_probe", mode="eval").body
            if ast.dump(node.values[index]) != ast.dump(expected):
                raise ValueError("runtime changed outside the explicit probe-state publication")
            del node.keys[index]
            del node.values[index]
            removed += 1
    if removed != 1 or ast.dump(versions[0]) != ast.dump(versions[1]):
        raise ValueError("ordinary model runtime differs beyond probe-state metadata")
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", old, new, "--", "beliefkv", "patches"],
        cwd=ROOT, text=True,
    ).splitlines()
    if changed != [path]:
        raise ValueError("other runtime or native changes separate the matched diagnostics")


def main():
    output = HERE / "current_model_review.json"
    if output.exists():
        raise FileExistsError("preserve earlier current-model review")
    seals = {}
    cpu = load(HERE / "formal_entry_r1_cpu.evidence.json", seals)
    manifest = load(Path(cpu["source_manifest"]["path"]), seals)
    if sha256_file(Path(cpu["source_manifest"]["path"])) != cpu["source_manifest"]["sha256"]:
        raise ValueError("current CPU manifest changed")
    for relative, digest in manifest.items():
        if sha256_file(APPLICATION / relative) != digest:
            raise ValueError("CPU-tested model/application source changed: " + relative)
    component = load(HERE / "gpu_components_r7.json", seals)
    validate_scenarios(component["scenarios"])
    if (
        component["status"] != "native_gpu_demand_prefill_components_qualified"
        or component["application"] != git_source_state(APPLICATION)
        or component["native_engine"] != cpu["native_engine"]
        or component["native_tree"] != DEMAND_PREFILL_PIPELINE_TREE
        or cpu["passing_unique_case_identities"] != 4100
    ):
        raise ValueError("current GPU/CPU sources do not match")
    deterministic = ROOT / "experiments/raw/demand_prefill_model_deterministic_20261028_r2"
    model = load(deterministic / "report.json", seals)
    if (
        model["status"] != "unforced_demand_prefill_deterministic_model_qualified"
        or model["application"] != component["application"]
        or model["native_engine"] != component["native_engine"]
        or model["component_sha256"] != sha256_file(HERE / "gpu_components_r7.json")
        or model["operator_sha256"] != sha256_file(APPLICATION / "scripts/qualify_demand_prefill_model.py")
    ):
        raise ValueError("current deterministic model proof differs")
    validate_model_parameters(
        load(deterministic / "server_info.json", seals), deterministic=True, capacity=61440,
    )
    _, values = responses(deterministic, seals, warm=False)
    numerical = compare_outputs(values)
    if numerical["maximum_logprob_delta"] != {"a_resident": 0., "b_demand": 0.}:
        raise ValueError("current exact deterministic numerical cohort changed")
    for relative in (
        "arm/opportunities/admission_opportunities.jsonl", "arm/server/runtime_audit.jsonl",
        "arm/server/transfer_telemetry.jsonl",
    ):
        seals[str(deterministic / relative)] = sha256_file(deterministic / relative)
    journals = records(deterministic / "arm/opportunities/admission_opportunities.jsonl")
    workers = records(deterministic / "arm/server/runtime_audit.jsonl")
    transfers = records(deterministic / "arm/server/transfer_telemetry.jsonl")
    progress = verify_progress(journals, workers)
    staged = inspect(journals, transfers, bytes_per_token=98304)
    service = service_evidence(staged["episodes"], journals, workers)
    exited(deterministic, model, seals)
    diagnostics, bodies = {}, {}
    for label, directory in (
        ("pipeline", ROOT / "experiments/raw/demand_prefill_model_continuation_20261028_r1"),
        ("native", ROOT / "experiments/raw/demand_prefill_native_current_continuation_20261028_r1"),
    ):
        report = load(directory / "report.json", seals)
        if label == "pipeline" and report["status"] != "failed" or label == "native" and (
            report["status"] != "native_demand_model_diagnostic_collected"
            or report["model"]["model_qualified"] is not False
        ):
            raise ValueError("ordinary-math diagnostic status changed")
        validate_model_parameters(
            load(directory / "server_info.json", seals), deterministic=False, capacity=61440,
        )
        bodies[label], rows = responses(directory, seals, warm=True)
        diagnostics[label] = output_diagnostics(rows)
        if (
            diagnostics[label]["a_resident"]["output_token_ids_equal"]
            or not diagnostics[label]["b_demand"]["output_token_ids_equal"]
            or diagnostics[label]["b_demand"]["maximum_logprob_delta"] > .05
            or not all(diagnostics[label][key]["maximum_logprob_delta"] == 0.
                       for key in ("a_initial", "b_initial"))
            or report["native_engine"] != component["native_engine"]
            or report["source_requests"] != model["source_requests"]
        ):
            raise ValueError("matched ordinary-math diagnostic cohort changed")
        if label == "pipeline":
            runtime_projection(report["application"]["commit"], model["application"]["commit"])
        exited(directory, report, seals)
    for label in bodies["pipeline"]:
        if {
            key: value for key, value in bodies["pipeline"][label].items() if key != "session_id"
        } != {
            key: value for key, value in bodies["native"][label].items() if key != "session_id"
        }:
            raise ValueError("ordinary-math diagnostic changed input or sampler: " + label)
    if subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"], text=True,
    ).strip():
        raise ValueError("model qualification has not released the GPU")
    if any(sha256_file(Path(path)) != digest for path, digest in seals.items()):
        raise ValueError("current model inputs changed before publication")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "current_cpu_and_components_verified": True, "passing_cpu_cases": 4100,
        "deterministic_model_exact_outputs": numerical,
        "normal_dma_model_progress": progress, "native_ack": staged,
        "subsequent_native_service": service, "ordinary_math_diagnostics": diagnostics,
        "ordinary_math_diagnostic_bodies_match_except_sessions": True,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "deterministic_fixture_only": True, "eligible_for_position_qualification": True,
        "whole_workload_qualified": False, "performance_accepted": False,
        "all_model_servers_exited": True, "gpu_compute_processes_absent": True,
        "inputs_sha256": seals, "operator_sha256": sha256_file(Path(__file__)),
        "scope": "Current KV I/O under deterministic math and matched ordinary diagnostics; no JCT claim.",
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print("Verified current deterministic KV I/O, ordinary diagnostics and actual owned exit.")


if __name__ == "__main__":
    main()
