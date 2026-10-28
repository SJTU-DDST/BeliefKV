"""Seal complete demand-pipeline performance and critical-path mechanism evidence."""

from datetime import datetime, timezone
import gzip
import importlib.util
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    output = HERE / "final_index.json"
    if output.exists():
        raise FileExistsError("preserve original complete demand-pipeline index")
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
    proof = complete["proofs"]["Full (demand pipeline)"]
    if (
        not paths["same_request_work_verified"] or not service["same_exact_request_work_verified"]
        or (complete["requests"], complete["prompt_tokens"], complete["output_tokens"])
        != (3674, 145565966, 549671)
        or not guard["summary"]["all_cases_verified"]
        or not eos["summary"]["all_triggered_requests_observed_final"]
        or warm["collection_code_commit"] != proof["code_commit"]
        or warm["summary"]["tool_commands"] != 0
        or not mechanism["staged_demand_accounting"]["exact_physical_bytes_reconciled"]
        or not mechanism["staged_native_service"][
            "all_members_have_native_service_or_explicit_cancellation"
        ]
    ):
        raise ValueError("complete work, demand/service, disabled tool prefetch or EOS guard differs")
    for report in (complete, mechanism, guard):
        for source, digest in report["inputs_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("complete native/service/qualification inputs changed")
            inputs[source] = digest
    current, before = paths["arms"]["Full"], paths["arms"]["Preadmission"]
    if current["workflows"].keys() != before["workflows"].keys():
        raise ValueError("complete historical workflow population differs")
    same_chains = {
        identity: row["critical_path_requests"] == before["workflows"][identity]["critical_path_requests"]
        for identity, row in current["workflows"].items()
    }
    work = None
    for arm in paths["arms"].values():
        actual = causal.receipt_work(Path(arm["arm"]) / "replay/native_requests.jsonl")
        if work is not None and actual != work:
            raise ValueError("complete arms execute different request work or stopping")
        work = actual
    deltas = {
        name: current["mean_components_s"].get(name, 0.) - before["mean_components_s"].get(name, 0.)
        for name in current["mean_components_s"].keys() | before["mean_components_s"].keys()
    }
    jct_delta = current["mean_jct_s"] - before["mean_jct_s"]
    if not math.isclose(math.fsum(deltas.values()), jct_delta, abs_tol=1e-6):
        raise ValueError("critical-path component deltas do not reconstruct complete JCT")
    historical = ROOT / "experiments/reports/full_only_pressure_index_20261028"
    analyses = {}
    for name, filename in (
        ("phase", "phase_gap_review.py"), ("shape", "critical_decode_shape_review.py"),
    ):
        helper = historical / filename
        spec = importlib.util.spec_from_file_location("complete_demand_pipeline_" + name, helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        analysis = module.audit(service["arms"]["Full"], current)
        analyses[name] = analysis[0] if name == "shape" else analysis
        inputs[str(helper)] = sha256_file(helper)
        inputs.update(module.SOURCES)
    old_phase = load(historical / "phase_gap_review.json")["arms"]["before"][
        "critical_partitions"
    ]["critical_tool_successors"]["workflow_equal_weight_s"]
    phase_deltas = {
        name: value - old_phase[name]
        for name, value in analyses["phase"]["critical_partitions"][
            "critical_tool_successors"
        ]["workflow_equal_weight_s"].items()
    }
    native = mechanism["native_transfer_and_worker_review"]
    old_native = load(
        ROOT / "experiments/reports/tool_offload_before_admission_20261027/"
        "control_regression_audit.json"
    )["arms"]["before"]
    archives = {}
    for name in (
        "complete_jct_paths.json", "complete_service_interleaving.json",
        "complete_demand_h2d_service.json", "mechanism_review.json",
        "complete_sampled_restore_waits.json",
    ):
        source = HERE / name
        archive = HERE / (name + ".gz")
        if archive.exists():
            raise FileExistsError("preserve earlier complete archive")
        raw = source.read_bytes()
        with archive.open("xb") as stream:
            stream.write(gzip.compress(raw, mtime=0))
        if gzip.decompress(archive.read_bytes()) != raw:
            raise ValueError("complete archive differs from original evidence")
        archives[str(archive)] = {
            "original_sha256": sha256_file(source), "archive_sha256": sha256_file(archive),
        }
    join = warm["summary"]["wait_events"]["by_source"].get("join_ticket", {})
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete demand-pipeline system performance and observed mechanisms.",
        "requests": len(work), "all_normal_tails_retained": True,
        "same_exact_work_and_original_stopping_verified": True,
        "metrics": complete["metrics"], "comparisons": complete["comparisons"],
        "same_critical_request_chains_by_workflow": same_chains,
        "all_critical_request_chains_match_preadmission": all(same_chains.values()),
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
        "staged_native_demand": mechanism["staged_native_demand"]["counts"],
        "staged_native_service": mechanism["staged_native_service"]["counts"],
        "staged_demand_accounting": mechanism["staged_demand_accounting"],
        "layer_handoffs": native["handoff_events"],
        "handoff_consumer_h2d_bytes": native["handoff_consumer_h2d_bytes"],
        "physical_plan_review": complete["physical_plan_review"],
        "join_prefetch": {
            "commands": warm["summary"]["join_commands"],
            "verified_reused_bytes": warm["summary"]["by_source"]["join_ticket"][
                "full_verified_reused_bytes_known"
            ],
            **{field: join.get(field, 0) for field in (
                "observed_events", "events_with_early_reused_full",
                "early_started_and_reused_full_bytes",
            )},
        },
        "sibling_service_summary": mechanism["sibling_service"]["summary"],
        "grant_service_summary": mechanism["join_grants"]["summary"],
        "sampled_wait_summary": {name: arm["summary"] for name, arm in waits["arms"].items()},
        "known_guard_summary": guard["summary"], "final_eos_summary": eos["summary"],
        "archives": archives, "inputs_sha256": inputs,
        "operator_sha256": sha256_file(Path(__file__)),
        "performance_accepted": False, "historical_incremental_control_cpu_known": False,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "limitations": [
            "Native engines and prior Full policies differ; this is not an isolated module ablation.",
            "Each arm retains its actual blocking path; changed chains are reported, not discarded.",
            "Exact staging ACKs and native service do not establish the amount of hidden DMA.",
            "Worker spans and sampled restore waits do not measure exposed incremental control CPU.",
            "Known numerical failures and every normal workflow tail remain disclosed.",
            "Single-run metrics do not establish repeatable or all-baseline superiority.",
        ],
    }
    causal.reservation.write_json(output, result)
    print({
        "comparisons": complete["comparisons"],
        "mean_jct_delta_vs_preadmission_s": jct_delta,
        "critical_component_delta_vs_preadmission_s": deltas,
        "staged_native_demand": result["staged_native_demand"],
        "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
