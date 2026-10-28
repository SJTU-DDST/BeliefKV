"""Seal the completed diagnostics, historical comparison and separate CPU repair."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError("preserve final review")
    inputs = {}

    def load(path):
        inputs[str(path)] = sha256_file(path)
        return json.loads(path.read_bytes())

    complete = load(HERE / "complete_review.json")
    mechanism = load(HERE / "mechanism_review.json")
    current_paths = load(HERE / "complete_jct_paths.json")
    current_service = load(HERE / "complete_service_interleaving.json")
    warm = load(HERE / "complete_wait_prefetch_summary.json")
    waits = load(HERE / "complete_sampled_restore_waits.json")
    before_dir = ROOT / "experiments/reports/tool_offload_before_admission_20261027"
    old_paths = load(before_dir / "complete_jct_paths.json")
    historical = load(before_dir / "control_regression_audit.json")
    before, current = old_paths["arms"]["Full"], current_paths["arms"]["Diagnostic"]
    if (
        not current_paths["same_request_work_verified"]
        or not current_service["same_exact_request_work_verified"]
        or before["workflows"].keys() != current["workflows"].keys()
        or any(
            before["workflows"][identity]["critical_path_requests"]
            != current["workflows"][identity]["critical_path_requests"]
            for identity in current["workflows"]
        )
    ):
        raise ValueError("complete work or critical request chain changed")
    old_work = causal.receipt_work(Path(before["arm"]) / "replay/native_requests.jsonl")
    new_work = causal.receipt_work(Path(current["arm"]) / "replay/native_requests.jsonl")
    if old_work != new_work or not len(new_work) == complete["requests"] == 3674:
        raise ValueError("historical and diagnostic exact work differ")
    for report in (complete, mechanism):
        for path, digest in report["inputs_sha256"].items():
            if sha256_file(Path(path)) != digest:
                raise ValueError("completed audit source changed")
    if warm["collection_code_commit"] != complete["proofs"]["Full (profiled)"]["code_commit"]:
        raise ValueError("reuse audit has a different collection application")
    cpu = load(HERE / "join_host_tail_shared_cpu.evidence.json")
    sources = load(Path(cpu["source_manifest"]["path"]))
    if cpu["passing_cases_including_subtests"] != 3705 or any(
        sha256_file(ROOT / path) != digest for path, digest in sources.items()
    ):
        raise ValueError("main repair differs from source-bound CPU qualification")
    archives = {}
    for name in (
        "complete_jct_paths.json", "complete_service_interleaving.json",
        "complete_sampled_restore_waits.json", "mechanism_review.json",
    ):
        path = HERE / name
        archived = path.with_suffix(path.suffix + ".gz")
        if gzip.decompress(archived.read_bytes()) != path.read_bytes():
            raise ValueError("archive changed original evidence bytes")
        archives[name] = {
            "uncompressed_sha256": sha256_file(path), "archive_sha256": sha256_file(archived),
        }
    deltas = {
        name: current["mean_components_s"].get(name, 0.) - before["mean_components_s"].get(name, 0.)
        for name in current["mean_components_s"].keys() | before["mean_components_s"].keys()
    }
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Completed diagnostic evidence and independent CPU repair, not causal attribution.",
        "same_exact_work_and_critical_request_chains": True,
        "diagnostic_application": complete["proofs"]["Full (profiled)"]["code_commit"],
        "separate_cpu_repair_commit": "a3d38d23",
        "main_cpu_qualification_cases": 3705,
        "mean_jct_delta_vs_preadmission_s": current["mean_jct_s"] - before["mean_jct_s"],
        "mean_critical_components_delta_vs_preadmission_s": deltas,
        "original_grouped_tool_successor_delta_s": historical[
            "critical_delta_workflow_equal_weight_s"
        ]["critical_tool_successors"],
        "physical_attempt_outcomes": complete["diagnostics"]["physical_attempt_outcomes"],
        "join_prefetch_reuse_summary": warm["summary"],
        "sampled_wait_arm_names": list(waits["arms"]),
        "archives": archives, "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "historical_incremental_control_cpu_known": False,
        "performance_accepted": False, "next_gpu_job_registered": False,
        "limitations": [
            "Original 24.98s belongs to grouped913e3238, not the current diagnostic.",
            "Measured sample CPU is neither expanded total CPU nor exposed GPU delay.",
            "Current GPU work excludes the later CPU-qualified Host-only-tail repair.",
            "Engine/profiling differences and single runs prevent isolated causal claims.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "same_exact_work_and_critical_request_chains": True,
        "jct_delta_s": result["mean_jct_delta_vs_preadmission_s"],
        "source_bound_cpu_cases": 3705, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
