"""Seal complete Full evidence, historical comparisons and preserved audit limits."""

from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
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
        raise FileExistsError("preserve original final index")
    inputs = {}

    def load(path):
        inputs[str(path)] = sha256_file(path)
        return causal.load(path)

    complete = load(HERE / "complete_review.json")
    mechanism = load(HERE / "mechanism_review.json")
    paths = load(HERE / "complete_jct_paths.json")
    service = load(HERE / "complete_service_interleaving.json")
    warm = load(HERE / "complete_wait_prefetch_summary.json")
    waits = load(HERE / "complete_sampled_restore_waits.json")
    guard = load(HERE / "complete_guard_cases.json")
    eos = load(HERE / "complete_eos_trigger_lifecycle.json")
    if (
        not paths["same_request_work_verified"] or not service["same_exact_request_work_verified"]
        or (complete["requests"], complete["prompt_tokens"], complete["output_tokens"])
        != (3674, 145565966, 549671)
        or not guard["summary"]["all_cases_verified"]
        or not eos["summary"]["all_triggered_requests_observed_final"]
        or warm["collection_code_commit"] != complete["proofs"]["Full (pressure index)"]["code_commit"]
    ):
        raise ValueError("complete work, trigger guard or collection identities differ")
    for report in (complete, mechanism, guard):
        for source, digest in report["inputs_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("complete native/service/qualification input changed")
            inputs[source] = digest
    current, before = paths["arms"]["Full"], paths["arms"]["Preadmission"]
    if current["workflows"].keys() != before["workflows"].keys():
        raise ValueError("historical workflow population changed")
    same_chains = {
        identity: row["critical_path_requests"] == before["workflows"][identity]["critical_path_requests"]
        for identity, row in current["workflows"].items()
    }
    exact = None
    for arm in paths["arms"].values():
        observed = causal.receipt_work(Path(arm["arm"]) / "replay/native_requests.jsonl")
        if exact is not None and observed != exact:
            raise ValueError("complete arms have different exact work or original stopping")
        exact = observed
    deltas = {
        name: current["mean_components_s"].get(name, 0.) - before["mean_components_s"].get(name, 0.)
        for name in current["mean_components_s"].keys() | before["mean_components_s"].keys()
    }
    jct_delta = current["mean_jct_s"] - before["mean_jct_s"]
    if not math.isclose(math.fsum(deltas.values()), jct_delta, abs_tol=1e-6):
        raise ValueError("complete critical components do not reconstruct the JCT difference")
    phases_path, shape_path = HERE / "phase_gap_review.py", HERE / "critical_decode_shape_review.py"
    analyses = {}
    for name, helper in (("phase", phases_path), ("shape", shape_path)):
        spec = importlib.util.spec_from_file_location("current_pressure_index_" + name, helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        analysis = module.audit(service["arms"]["Full"], current)
        analyses[name] = analysis[0] if name == "shape" else analysis
        inputs[str(helper)] = sha256_file(helper)
        inputs.update(module.SOURCES)
    old_phase = load(HERE / "phase_gap_review.json")["arms"]["before"][
        "critical_partitions"
    ]["critical_tool_successors"]["workflow_equal_weight_s"]
    phase_deltas = {
        name: value - old_phase[name]
        for name, value in analyses["phase"]["critical_partitions"][
            "critical_tool_successors"
        ]["workflow_equal_weight_s"].items()
    }
    archives = {}
    for name in (
        "complete_jct_paths.json", "complete_service_interleaving.json",
        "mechanism_review.json", "complete_sampled_restore_waits.json",
    ):
        original, archive = HERE / name, HERE / (name + ".gz")
        if gzip.decompress(archive.read_bytes()) != original.read_bytes():
            raise ValueError("archive differs from original complete evidence")
        archives[str(archive)] = {
            "original_sha256": sha256_file(original), "archive_sha256": sha256_file(archive),
        }
    figures = ROOT / "experiments/figures/full_only_pressure_index_20261028_r1"
    for name in ("time_bins.csv", "geometry.csv"):
        original, archive = figures / name, figures / (name + ".gz")
        raw = gzip.decompress(archive.read_bytes())
        if raw.replace(b"\r\n", b"\n") != original.read_bytes():
            raise ValueError("CSV changed beyond line-ending normalization")
        archives[str(archive)] = {
            "raw_csv_sha256": hashlib.sha256(raw).hexdigest(),
            "archive_sha256": sha256_file(archive),
            "normalized_csv_sha256": sha256_file(original),
        }
    for name in ("decode_timeline.png", "decode_distributions.png", "decode_geometry.png"):
        inputs[str(figures / name)] = sha256_file(figures / name)
    native = mechanism["native_transfer_and_worker_review"]
    old_native = load(
        ROOT / "experiments/reports/tool_offload_before_admission_20261027/control_regression_audit.json"
    )["arms"]["before"]
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete same-work Full and historical service diagnostics, not isolated module effects.",
        "requests": len(exact), "all_normal_tails_retained": True,
        "same_exact_work_and_original_stopping_verified": True,
        "same_critical_request_chains_by_workflow": same_chains,
        "all_critical_request_chains_match_preadmission": all(same_chains.values()),
        "metrics": complete["metrics"],
        "mean_jct_delta_vs_preadmission_s": jct_delta,
        "critical_component_delta_vs_preadmission_s": deltas,
        "current_outside_member_phase_review": analyses["phase"],
        "critical_tool_other_phase_delta_vs_preadmission_s": phase_deltas,
        "current_critical_tool_decode_shape": analyses["shape"],
        "native_transfers": native["transfers"],
        "transfer_delta_vs_preadmission_gb_decimal": {
            direction: native["transfers"][direction + "_bytes"] / 1e9
            - old_native[direction + "_gb_decimal"] for direction in ("h2d", "d2h")
        },
        "layer_handoffs": native["handoff_events"],
        "handoff_consumer_h2d_bytes": native["handoff_consumer_h2d_bytes"],
        "join_prefetch": {
            "commands": warm["summary"]["join_commands"],
            "verified_reused_bytes": warm["summary"]["by_source"]["join_ticket"][
                "full_verified_reused_bytes_known"
            ],
            **{field: warm["summary"]["wait_events"]["by_source"]["join_ticket"][field]
               for field in ("observed_events", "events_with_early_reused_full",
                             "early_started_and_reused_full_bytes")},
        },
        "sibling_service_summary": mechanism["sibling_service"]["summary"],
        "grant_service_summary": mechanism["join_grants"]["summary"],
        "sampled_wait_summary": {name: arm["summary"] for name, arm in waits["arms"].items()},
        "known_guard_summary": guard["summary"], "final_eos_summary": eos["summary"],
        "archives": archives, "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "local_cpu_speedup_establishes_jct_benefit": False,
        "historical_incremental_control_cpu_known": False,
        "performance_accepted": False, "next_gpu_job_registered": False,
        "limitations": [
            "The reused control has a different reviewed native engine.",
            "Both implementation fixes changed together; this is not a publication or ancestor ablation.",
            "Worker-phase and shape partitions include runtime and waits, not pure kernels or exposed CPU.",
            "Sampled restore cohorts differ and are never expanded to whole-run waits.",
            "Unproven grant/session identities remain unknown; cumulative releases are not saved JCT.",
            "One modest combined-system result does not satisfy clear, repeatable, all-baseline gains.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "mean_jct_delta_vs_preadmission_s": jct_delta,
        "critical_component_delta_vs_preadmission_s": deltas,
        "all_critical_chains_equal": all(same_chains.values()),
        "critical_tool_other_phase_delta_s": phase_deltas,
        "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
