"""Review complete TokenCake and retain the sealed Full/KVFlow/ThunderAgent."""

import argparse
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
REGISTRATION = OPS / "tokencake_locobench_native_hooks_20261028_r1.registration.json"
FIGURES = ROOT / "experiments/figures/tokencake_locobench_native_hooks_20261028_r1"
PRIOR_REVIEW = ROOT / "experiments/reports/kvflow_radix_session_20261028/complete_review.json"
PRIOR_FIGURES = ROOT / "experiments/figures/kvflow_locobench_v230_v311_20261028_r1/evidence.json.gz"
SPECS = (
    ("Full ACK repair", OPS / "demand_prefill_ack_retry_20261028_r1.registration.json", "full"),
    ("KVFlow observed", OPS / "kvflow_locobench_v230_v311_20261028_r1.registration.json", "kvflow_observed_graph"),
    ("ThunderAgent decay", OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json", "thunderagent_acting_decay"),
    ("TokenCake", REGISTRATION, "tokencake_paper_observed"),
)
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import (
    COMMON_TREE, DEMAND_PREFILL_PIPELINE_TREE, TOKENCAKE_TREE, engine_patch,
)
from beliefkv.experiments.paper_baselines import (
    tokencake_parameters, validate_state, validate_tokencake_parameters,
)
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.tokencake_engine import verify_native_hooks
from scripts import plot_causal_decode_diagnostics as plotting
from scripts import run_causal_comparison as causal
from scripts.qualify_paper_prefix_transfers import validate_report as validate_prefix
from scripts.qualify_tokencake_runtime import validate_report as validate_lifecycle


spec = importlib.util.spec_from_file_location(
    "tokencake_complete_common",
    ROOT / "experiments/reports/prefill_pipeline_20261028/complete_review.py",
)
BASE = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BASE)
TREES = {
    "Full ACK repair": DEMAND_PREFILL_PIPELINE_TREE,
    "KVFlow observed": COMMON_TREE, "ThunderAgent decay": COMMON_TREE,
    "TokenCake": TOKENCAKE_TREE,
}


def validate_contracts(plans):
    expected = BASE.common_contract(plans["Full ACK repair"])
    for name, plan in plans.items():
        if BASE.common_contract(plan) != expected or plan["engine_tree"] != TREES[name]:
            raise ValueError("complete work, tools, seeds, capacity or reviewed native engine differs")


def precheck():
    plans = {name: causal.load(path) for name, path, _ in SPECS}
    validate_contracts(plans)
    for name, path, label in SPECS[:-1]:
        BASE.completed(plans[name], path, label, TREES[name])
    state = causal.load(REGISTRATION.with_suffix(".state.json"))
    if state["status"] == "completed":
        raise ValueError("use complete review after terminal completion")
    try:
        BASE.completed(plans["TokenCake"], REGISTRATION, SPECS[-1][2], TOKENCAKE_TREE)
    except ValueError:
        return {
            "common_complete_work_contract_matches": True,
            "three_complete_controls_accepted": True,
            "incomplete_tokencake_rejected": True,
            "performance_accepted": False,
        }
    raise ValueError("incomplete TokenCake was accepted as a complete comparison")


def main():
    output = HERE / "complete_review.json"
    if output.exists() or FIGURES.exists():
        raise FileExistsError("preserve original complete TokenCake review and figures")
    plans = {name: causal.load(path) for name, path, _ in SPECS}
    validate_contracts(plans)
    work, proofs, inputs, summaries = None, {}, {}, {}
    for name, path, label in SPECS:
        plan = plans[name]
        BASE.completed(plan, path, label, TREES[name])
        arm = Path(plan["run_root"]) / label
        summary_path = arm / "replay/summary.json"
        summaries[name] = causal.load(summary_path)
        actual = causal.validate_result(plan, summaries[name], arm, live_control=True)
        if work is not None and actual != work:
            raise ValueError("complete baseline request work or original stopping differs")
        work = actual
        proofs[name] = BASE.observe_registered(plan, path, label, arm)
        BASE.drain_and_exit(arm)
        info = causal.load(arm / "server/server_info.json")
        if any(info.get(field) != value for field, value in {
            "enable_deterministic_inference": False,
            "attention_backend": "fa3", "sampling_backend": "flashinfer",
            "enable_custom_logit_processor": True,
        }.items()):
            raise ValueError("formal original numerical/position mode differs")
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
    plan = plans["TokenCake"]
    arm, frozen = Path(plan["run_root"]) / SPECS[-1][2], Path(plan["execution_checkout"])
    runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
    validate_state(SPECS[-1][2], runtime)
    parameters = tokencake_parameters(plan["reference_environment"])
    validate_tokencake_parameters(runtime["paper_baseline"], parameters)
    hooks = verify_native_hooks(Path(plan["engine_checkout"]))
    patch = engine_patch(frozen, TOKENCAKE_TREE)
    qualifiers = {}
    for name, directory, validator in (
        ("prefix", "native_prefix_qualification", validate_prefix),
        ("lifecycle", "native_tokencake_qualification", validate_lifecycle),
    ):
        path = arm / directory / "summary.json"
        validator(
            causal.load(path), engine_tree=TOKENCAKE_TREE,
            patch_sha256=sha256_file(patch), root=frozen,
        )
        qualifiers[name] = {"path": str(path), "sha256": sha256_file(path)}
        inputs[str(path)] = sha256_file(path)
    prior = causal.load(PRIOR_REVIEW)
    if sha256_file(PRIOR_FIGURES) != prior["inputs_sha256"][str(PRIOR_FIGURES)]:
        raise ValueError("complete control figures changed")
    cached = json.loads(gzip.decompress(PRIOR_FIGURES.read_bytes()))["arms"]
    arms = {name: cached[name] for name, _, _ in SPECS[:-1]}
    for name, row in arms.items():
        if row["match_contract"] != plotting.match_contract(plans[name], work):
            raise ValueError("cached control result belongs to another complete contract")
        plotting.require_complete_prefixes(row["input_jsonl_prefixes"])
        for source, digest in row["evidence_sha256"].items():
            if sha256_file(Path(source)) != digest:
                raise ValueError("cached complete control input changed")
            inputs[source] = digest
    current = plotting.extract("TokenCake", REGISTRATION, arm, bin_s=60)
    arms["TokenCake"] = current
    for name, row in arms.items():
        if row["match_contract"]["engine_tree"] != TREES[name]:
            raise ValueError("unreviewed three-engine comparison")
    plotting.require_matching({
        name: {"match_contract": {
            key: value for key, value in row["match_contract"].items() if key != "engine_tree"
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
    for name in ("KVFlow observed", "ThunderAgent decay", "TokenCake"):
        full, baseline = metrics["Full ACK repair"], metrics[name]
        comparisons[name] = {
            "full_mean_jct_reduction_percent": 100. * (1. - full["mean_jct_s"] / baseline["mean_jct_s"]),
            "full_output_throughput_increase_percent": 100. * (
                full["end_to_end_output_tokens_per_s"] / baseline["end_to_end_output_tokens_per_s"] - 1.
            ),
        }
    FIGURES.mkdir(parents=True)
    plotted = {name: arms[name] for name in (
        "Full ACK repair", "TokenCake", "ThunderAgent decay",
    )}
    figures = plotting.render(
        plotted, FIGURES, bin_s=60,
        title="Complete V230/V311: Full / TokenCake / ThunderAgent (reviewed engines differ)",
    )
    for filename, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        BASE.write_csv(FIGURES / filename, [
            {"arm": name, **row} for name, data in arms.items() for row in data[field]
        ])
    with (FIGURES / "evidence.json.gz").open("xb") as stream:
        stream.write(gzip.compress(json.dumps({
            "arms": arms, "figures": figures, "all_normal_tails_retained": True,
            "reviewed_native_engines": TREES, "plotted_arms": list(plotted),
            "performance_accepted": False,
        }, indent=2, allow_nan=False).encode(), mtime=0))
    inputs.update(current["evidence_sha256"])
    for source in (Path(__file__), Path(BASE.__file__), PRIOR_REVIEW, PRIOR_FIGURES, *FIGURES.iterdir()):
        inputs[str(source)] = sha256_file(source)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "requests": len(work), "prompt_tokens": 145565966, "output_tokens": 549671,
        "exact_complete_work_and_native_stopping_verified": True,
        "all_normal_tails_retained": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "registered_configuration_observers": proofs, "metrics": metrics,
        "full_comparisons": comparisons, "tokencake_actual_parameters": parameters,
        "tokencake_actual_paper_state": runtime["paper_baseline"],
        "native_scheduler_hooks": hooks, "fresh_physical_qualifiers": qualifiers,
        "figures": figures, "inputs_sha256": inputs, "performance_accepted": False,
        "limitations": [
            "V311 timing is synthetic observable-category timing.",
            "KVFlow uses already observed graph events and completed-input prefix history.",
            "Three reviewed native engines differ; this is a combined-system comparison.",
            "Single completed baseline controls do not establish statistical significance.",
            "Agentix formal model/pressure qualification and complete results remain outstanding.",
            "Decode worker intervals include runtime and waits, not pure kernel execution.",
        ],
    }
    causal.reservation.write_json(output, result)
    print(json.dumps({"metrics": metrics, "full_comparisons": comparisons}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precheck", action="store_true")
    arguments = parser.parse_args()
    if arguments.precheck:
        print(json.dumps(precheck(), indent=2))
    else:
        main()
