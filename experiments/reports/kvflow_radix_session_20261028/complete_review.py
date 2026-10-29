"""Review complete KVFlow against the sealed Full and ThunderAgent controls."""

from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "kvflow_locobench_v230_v311_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
FULL = OPS / "demand_prefill_ack_retry_20261028_r1.registration.json"
PRIOR_INDEX = ROOT / "experiments/reports/prefill_pipeline_20261028/readmission_complete/final_index.json"
PRIOR_FIGURES = ROOT / "experiments/figures/demand_prefill_ack_retry_20261028_r1/evidence.json.gz"
FIGURES = ROOT / "experiments/figures/kvflow_locobench_v230_v311_20261028_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import COMMON_TREE, DEMAND_PREFILL_PIPELINE_TREE
from beliefkv.experiments.paper_baselines import validate_state
from beliefkv.experiments.source_provenance import sha256_file
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal
from scripts.qualify_paper_prefix_transfers import validate_report


spec = importlib.util.spec_from_file_location(
    "kvflow_complete_common",
    ROOT / "experiments/reports/prefill_pipeline_20261028/complete_review.py",
)
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)


def main():
    output = HERE / "complete_review.json"
    if output.exists() or FIGURES.exists():
        raise FileExistsError("preserve original KVFlow review and figures")
    plans = {label: causal.load(path) for label, path in (
        ("KVFlow observed", REGISTRATION), ("Full ACK repair", FULL),
        ("ThunderAgent decay", CONTROL),
    )}
    expected_contract = BASE.common_contract(plans["KVFlow observed"])
    work, proofs, inputs, summaries = None, {}, {}, {}
    for name, path, arm_label, tree in (
        ("KVFlow observed", REGISTRATION, "kvflow_observed_graph", COMMON_TREE),
        ("Full ACK repair", FULL, "full", DEMAND_PREFILL_PIPELINE_TREE),
        ("ThunderAgent decay", CONTROL, "thunderagent_acting_decay", COMMON_TREE),
    ):
        plan = plans[name]
        BASE.completed(plan, path, arm_label, tree)
        if BASE.common_contract(plan) != expected_contract:
            raise ValueError("baseline source, timing, seed or capacity differs")
        arm = Path(plan["run_root"]) / arm_label
        summary_path = arm / "replay/summary.json"
        summaries[name] = causal.load(summary_path)
        actual = causal.validate_result(plan, summaries[name], arm, live_control=True)
        if work is not None and actual != work:
            raise ValueError("baseline request work or original stopping differs")
        work = actual
        proofs[name] = BASE.observe_registered(plan, path, arm_label, arm)
        BASE.drain_and_exit(arm)
        for source in (
            path, path.with_suffix(".state.json"), summary_path,
            arm / "server/server_info.json", arm / "telemetry_drain.json",
            arm / "owned_process_cleanup.json",
        ):
            inputs[str(source)] = sha256_file(source)
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("complete shared population changed")
    plan = plans["KVFlow observed"]
    arm = Path(plan["run_root"]) / "kvflow_observed_graph"
    runtime_path = arm / "opportunities/admission_opportunities.jsonl"
    runtime = causal.qualified.last_runtime_state(runtime_path)
    validate_state("kvflow_observed_graph", runtime)
    if runtime["paper_baseline"]["counts"].get("native_radix_session_requests", 0) != 3674:
        raise ValueError("complete KVFlow has missing native-session registrations")
    component_path = arm / "native_prefix_qualification/summary.json"
    frozen = Path(plan["execution_checkout"])
    patch = frozen / "patches/sglang-v0.5.20-beliefkv-staging.patch"
    validate_report(
        causal.load(component_path), engine_tree=COMMON_TREE,
        patch_sha256=sha256_file(patch), root=frozen,
    )
    prior = causal.load(PRIOR_INDEX)
    if sha256_file(PRIOR_FIGURES) != prior["inputs_sha256"][str(PRIOR_FIGURES)]:
        raise ValueError("completed Full/control figures changed")
    cached = json.loads(gzip.decompress(PRIOR_FIGURES.read_bytes()))["arms"]
    arms = {
        "Full ACK repair": cached["Full ACK repair"],
        "ThunderAgent decay": cached["ThunderAgent decay"],
    }
    for name, row in arms.items():
        if row["match_contract"] != plotting.match_contract(plans[name], work):
            raise ValueError("cached complete result belongs to another contract")
        plotting.require_complete_prefixes(row["input_jsonl_prefixes"])
        for source, digest in row["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("cached complete-arm input changed")
            inputs[source] = digest
    current = plotting.extract("KVFlow observed", REGISTRATION, arm, bin_s=60)
    arms = {
        "Full ACK repair": arms["Full ACK repair"], "KVFlow observed": current,
        "ThunderAgent decay": arms["ThunderAgent decay"],
    }
    if {row["match_contract"]["engine_tree"] for row in arms.values()} != {
        COMMON_TREE, DEMAND_PREFILL_PIPELINE_TREE,
    }:
        raise ValueError("unreviewed native engine pair")
    plotting.require_matching({
        name: {"match_contract": {
            field: value for field, value in row["match_contract"].items() if field != "engine_tree"
        }} for name, row in arms.items()
    })
    metrics = {
        name: {
            **{field: row[field] for field in (
                "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
            )},
            "streaming_response_metrics": {
                field: summaries[name]["streaming_response_metrics"][field] for field in (
                    "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
                )
            },
        } for name, row in arms.items()
    }
    comparisons = {}
    for name in ("KVFlow observed", "ThunderAgent decay"):
        full, baseline = metrics["Full ACK repair"], metrics[name]
        comparisons[name] = {
            "full_mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / baseline["mean_jct_s"]),
            "full_output_throughput_increase_percent": 100. * (
                full["end_to_end_output_tokens_per_s"] / baseline["end_to_end_output_tokens_per_s"] - 1.
            ),
        }
    FIGURES.mkdir(parents=True)
    figures = plotting.render(
        arms, FIGURES, bin_s=60,
        title="Complete V230/V311: Full / KVFlow / ThunderAgent decay (different Full engine)",
    )
    for filename, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        BASE.write_csv(FIGURES / filename, [
            {"arm": label, **row} for label, data in arms.items() for row in data[field]
        ])
    with (FIGURES / "evidence.json.gz").open("xb") as stream:
        stream.write(gzip.compress(json.dumps({
            "arms": arms, "figures": figures, "all_normal_tails_retained": True,
            "reviewed_engine_pair": [COMMON_TREE, DEMAND_PREFILL_PIPELINE_TREE],
            "performance_accepted": False,
        }, indent=2, allow_nan=False).encode(), mtime=0))
    inputs.update(current["evidence_sha256"])
    for source in (
        Path(__file__), Path(BASE.__file__), PRIOR_INDEX, PRIOR_FIGURES, component_path,
        *FIGURES.iterdir(),
    ):
        inputs[str(source)] = sha256_file(source)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "requests": len(work), "prompt_tokens": 145565966, "output_tokens": 549671,
        "exact_complete_work_and_native_stopping_verified": True,
        "all_normal_tails_retained": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "registered_configuration_observers": proofs,
        "metrics": metrics, "full_comparisons": comparisons,
        "kvflow_actual_paper_state": runtime["paper_baseline"],
        "fresh_prefix_component_validated": True, "figures": figures,
        "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "KVFlow uses already observed graph events and observed completed-input prefixes.",
            "The shared V311 tool durations are synthetic observable-category timings.",
            "Full has a different reviewed native engine; this is a combined-system comparison.",
            "One KVFlow and one ThunderAgent control do not establish statistical significance.",
            "TokenCake and Agentix complete formal results remain outstanding.",
            "Decode worker intervals include runtime and waits, not pure kernel execution.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({"metrics": metrics, "full_comparisons": comparisons}, indent=2))


if __name__ == "__main__":
    main()
