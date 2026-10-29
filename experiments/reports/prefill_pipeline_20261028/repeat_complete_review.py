"""Compare complete identical-source R1/R2 Full runs with the sealed V345 control."""

import argparse
from datetime import datetime, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "repeat_r2_complete"
FIGURES = ROOT / "experiments/figures/demand_prefill_pipeline_20261028_r2"


def helper(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = helper(HERE / "complete_review.py", "demand_repeat_complete_base")
REPEAT = helper(HERE / "repeat_registration.py", "demand_repeat_metadata")


def repeat_identity(first, second):
    changed = REPEAT.validate_metadata(first, second)
    if (
        second["after_registration"] != str(REPEAT.PRIOR)
        or second["repeat_of"]["registration_sha256"] != BASE.sha256_file(REPEAT.PRIOR)
        or second["repeat_of"]["entry_check_sha256"] != BASE.sha256_file(REPEAT.CHECK)
        or second["repeat_of"]["main_admission_retry_fix_included"] is not False
        or BASE.common_contract(first) != BASE.common_contract(second)
    ):
        raise ValueError("source-identical repeat provenance or common work differs")
    return changed


def precheck():
    output = HERE / "repeat_r2_complete_precheck.json"
    if output.exists():
        raise FileExistsError("preserve original repeat-completion precheck")
    first, second = (BASE.causal.load(path) for path in (REPEAT.PRIOR, REPEAT.TARGET))
    changed = repeat_identity(first, second)
    BASE.completed(first, REPEAT.PRIOR, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    try:
        BASE.completed(second, REPEAT.TARGET, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
    except ValueError as error:
        if str(error) != "require complete successful work and actual owned process exit":
            raise
        rejection = str(error)
    else:
        raise AssertionError("precheck requires an actual live repeat to reject")
    if OUTPUT.exists() or FIGURES.exists():
        raise AssertionError("precheck created or encountered complete repeat artifacts")
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Actual complete R1 acceptance, live R2 rejection and exact metadata identity.",
        "actual_complete_R1_and_owned_exit_verified": True,
        "actual_live_R2_rejected": rejection, "changed_metadata_only": changed,
        "inputs_sha256": {
            str(path): BASE.sha256_file(path) for path in (
                Path(__file__), HERE / "complete_review.py", HERE / "repeat_registration.py",
                REPEAT.PRIOR, REPEAT.TARGET, REPEAT.CHECK,
            )
        },
        "complete_performance_result_written": False, "performance_accepted": False,
    }
    BASE.causal.reservation.write_json(output, result)
    print({key: result[key] for key in (
        "actual_complete_R1_and_owned_exit_verified", "actual_live_R2_rejected",
        "complete_performance_result_written",
    )})


def main():
    first, second = (BASE.causal.load(path) for path in (REPEAT.PRIOR, REPEAT.TARGET))
    changed = repeat_identity(first, second)
    if OUTPUT.exists() or FIGURES.exists():
        raise FileExistsError("preserve earlier complete repeat evidence")
    inputs, work, clocks, observers, summaries = {}, None, {}, {}, {}
    for name, registration, plan in (
        ("Full R1", REPEAT.PRIOR, first), ("Full R2", REPEAT.TARGET, second),
    ):
        BASE.completed(plan, registration, "full", BASE.DEMAND_PREFILL_PIPELINE_TREE)
        arm = Path(plan["run_root"]) / "full"
        summary_path = arm / "replay/summary.json"
        summary = BASE.causal.load(summary_path)
        actual = BASE.causal.validate_result(plan, summary, arm, live_control=True)
        if work is not None and work != actual:
            raise ValueError("complete repeated requests, tokens or native stopping differ")
        work = actual
        observers[name] = BASE.observe_registered(plan, registration, "full", arm)
        BASE.drain_and_exit(arm)
        BASE.pipeline_state(BASE.causal.qualified.last_runtime_state(
            arm / "opportunities/admission_opportunities.jsonl",
        ))
        info = BASE.causal.load(arm / "server/server_info.json")
        if any(info.get(key) != value for key, value in {
            "enable_deterministic_inference": False, "attention_backend": "fa3",
            "sampling_backend": "flashinfer", "enable_custom_logit_processor": True,
        }.items()):
            raise ValueError("repeat changed actual formal numerical or sampling mode")
        clocks[name] = BASE.causal.replay_event_metrics(summary, arm, plan["workflow_ids"])
        summaries[name] = summary
        for path in (
            registration, registration.with_suffix(".state.json"), summary_path,
            arm / "telemetry_drain.json", arm / "owned_process_cleanup.json",
            arm / "server/server_info.json",
        ):
            inputs[str(path)] = BASE.sha256_file(path)
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("complete repeated workload population changed")
    complete_path = HERE / "complete_review.json"
    original = BASE.causal.load(complete_path)
    if not original["two_fresh_idle_snapshots_and_actual_owned_exit_verified"]:
        raise ValueError("original control review is incomplete")
    for source, digest in original["inputs_sha256"].items():
        if BASE.sha256_file(Path(source)) != digest:
            raise ValueError("sealed original model, control or result changed: " + source)
        inputs[source] = digest
    figure_evidence = ROOT / "experiments/figures/demand_prefill_pipeline_20261028_r1/evidence.json.gz"
    cached = json.loads(gzip.decompress(figure_evidence.read_bytes()))
    if (
        cached["registration_sha256"][str(REPEAT.PRIOR)] != BASE.sha256_file(REPEAT.PRIOR)
        or cached["registration_sha256"][str(BASE.CONTROL)] != BASE.sha256_file(BASE.CONTROL)
    ):
        raise ValueError("cached R1 or V345 evidence belongs to another registration")
    control_plan = BASE.causal.load(BASE.CONTROL)
    BASE.completed(control_plan, BASE.CONTROL, "thunderagent_acting_decay", BASE.COMMON_TREE)
    from scripts import plot_causal_decode_diagnostics as plotting

    arms = {
        "Full R2": plotting.extract(
            "Full R2", REPEAT.TARGET, Path(second["run_root"]) / "full", bin_s=60,
        ),
        "Full R1": cached["arms"]["Full (demand pipeline)"],
    }
    plotting.require_matching(arms)
    metrics = {}
    for name, arm in arms.items():
        if (
            abs(clocks[name]["mean_jct_ms"] - arm["mean_jct_s"] * 1000.) > .01
            or abs(clocks[name]["workflow_event_makespan_ms"] - arm["makespan_s"] * 1000.) > .01
        ):
            raise ValueError("complete workflow clocks and worker evidence disagree")
        metrics[name] = {key: arm[key] for key in (
            "mean_jct_s", "makespan_s", "end_to_end_output_tokens_per_s", "decode",
        )}
        metrics[name]["streaming_response_metrics"] = {
            key: summaries[name]["streaming_response_metrics"][key] for key in (
                "workflow_ttft_ms", "workflow_max_post_first_ready_output_gap_ms",
            )
        }
        inputs.update(arm["evidence_sha256"])
    metrics["ThunderAgent decay"] = original["metrics"]["ThunderAgent decay"]
    comparisons = {}
    for name in ("Full R1", "Full R2"):
        row, control = metrics[name], metrics["ThunderAgent decay"]
        comparisons[name] = {
            "mean_jct_reduction_percent": 100. * (1. - row["mean_jct_s"] / control["mean_jct_s"]),
            "end_to_end_output_increase_percent": 100. * (
                row["end_to_end_output_tokens_per_s"] / control["end_to_end_output_tokens_per_s"] - 1.
            ),
        }
    FIGURES.mkdir(parents=True)
    figures = plotting.render(
        arms, FIGURES, bin_s=60,
        title="Same V311 work, source and engine: Full R2 / Full R1",
    )
    for name, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
        BASE.write_csv(FIGURES / name, [
            {"arm": label, **row} for label, arm in arms.items() for row in arm[field]
        ])
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms, "figures": figures,
        "registration_sha256": {
            str(path): BASE.sha256_file(path) for path in (REPEAT.PRIOR, REPEAT.TARGET)
        },
        "all_normal_tails_retained": True, "performance_accepted": False,
        "operator_sha256": BASE.sha256_file(Path(__file__)),
    }
    with (FIGURES / "evidence.json.gz").open("xb") as stream:
        stream.write(gzip.compress(json.dumps(evidence, indent=2, allow_nan=False).encode(), mtime=0))
    for path in (*FIGURES.iterdir(), Path(__file__), HERE / "complete_review.py",
                 HERE / "repeat_registration.py", complete_path, figure_evidence):
        inputs[str(path)] = BASE.sha256_file(path)
    paired = {
        identity: (value - clocks["Full R1"]["workflow_jct_ms"][identity]) / 1000.
        for identity, value in sorted(clocks["Full R2"]["workflow_jct_ms"].items())
    }
    OUTPUT.mkdir(parents=True)
    result = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Complete identical-source Full repetition versus one sealed V345 control.",
        "requests": len(work), "prompt_tokens": sum(row[0] for row in work.values()),
        "output_tokens": sum(row[1] for row in work.values()),
        "exact_work_and_original_stopping_verified": True,
        "all_normal_tails_retained": True,
        "two_fresh_idle_snapshots_and_actual_owned_exit_verified": True,
        "changed_metadata_only": changed, "registered_configuration_observers": observers,
        "metrics": metrics, "comparisons_vs_same_V345": comparisons,
        "repeat_minus_R1_workflow_jct_s": paired,
        "repeat_faster_workflows": sum(value < 0 for value in paired.values()),
        "repeat_slower_workflows": sum(value > 0 for value in paired.values()),
        "both_repeats_improve_both_metrics": all(
            value["mean_jct_reduction_percent"] > 0
            and value["end_to_end_output_increase_percent"] > 0
            for value in comparisons.values()
        ),
        "main_admission_retry_fix_included": False,
        "ordinary_math_long_greedy_equivalence_verified": False,
        "figures": figures, "inputs_sha256": inputs,
        "performance_accepted": False,
        "limitations": [
            "Only the Full candidate is repeated; the reused V345 control has one complete run.",
            "Original Full/V345 native engines differ; only R1/R2 source, policy and engine are identical.",
            "All normal tails remain; actual partial bins are retained without post-end zero padding.",
            "Worker intervals include runtime and waits, not pure kernel or exposed control CPU.",
            "Positive signs alone do not establish a large gain or all-baseline superiority.",
            "New main's ACK retry repair is excluded and gains no GPU qualification from this repeat.",
            "Native demand ACK/service and JOIN/EOS mechanism audits remain separately required.",
        ],
    }
    BASE.causal.reservation.write_json(OUTPUT / "complete_review.json", result)
    print(json.dumps({
        "metrics": metrics, "comparisons_vs_same_V345": comparisons,
        "both_repeats_improve_both_metrics": result["both_repeats_improve_both_metrics"],
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precheck", action="store_true")
    if parser.parse_args().precheck:
        precheck()
    else:
        main()
