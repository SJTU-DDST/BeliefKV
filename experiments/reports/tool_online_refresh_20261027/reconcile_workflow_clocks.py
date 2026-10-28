"""Reconcile the initial refresh audit with the declared workflow metric clock."""

from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT.parent / "ops"
INITIAL = Path(__file__).with_name("refresh_audit.json")
CONTROL = "v345_locobench_categorized_thunder_decay_20261027"
initial = json.loads(INITIAL.read_bytes())
sys.path.insert(0, initial["measured_frozen_checkout"])

from beliefkv.experiments.source_provenance import sha256_file
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    initial_operator = INITIAL.with_suffix(".py")
    if sha256_file(initial_operator) != initial["operator_sha256"]:
        raise ValueError("initial audit operator changed")
    control_path = OPS / (CONTROL + ".registration.json")
    control = causal.load(control_path)
    names = [*initial["runs"], CONTROL]
    runs = {}
    for name in names:
        path = OPS / (name + ".registration.json")
        plan = causal.load(path)
        for field in (
            "source_plan", "token_source_sha256", "source_capacity",
            "source_server_parameters", "workflow_ids", "quality_exclusions",
            "quality_exclusion_details", "tool_timing_overlay", "sampling_mode",
            "stream_response_metrics", "request_timeout_s", "max_running_requests",
            "engine_tree",
        ):
            if plan.get(field) != control.get(field):
                raise ValueError("changed comparison field: " + field)
        if plan["quality_exclusions"] or not causal.predecessor_ready({
            **plan, "after_registration": str(path),
        }):
            raise ValueError("incomplete run or excluded normal tail")
        causal.comparison.verify_sources(plan)
        arm_name = "thunderagent_acting_decay" if name == CONTROL else "full"
        arm = Path(plan["run_root"]) / arm_name
        summary_path = arm / "replay/summary.json"
        summary = causal.load(summary_path)
        if (
            summary["completed_requests"] != plan["source_plan"]["requests"]
            or summary["exact_tokens_verified"] is not True
        ):
            raise ValueError("changed or incomplete replay work")
        reader = SealedRecords()
        events_path = arm / "replay/runtime_events.replay.jsonl"
        metrics = causal.workflow_event_metrics(reader(events_path), plan["workflow_ids"])
        if any(
            abs(summary["workflow_elapsed_ms"][key] - elapsed) > 1.
            for key, elapsed in metrics["workflow_jct_ms"].items()
        ):
            raise ValueError("summary and workflow-event JCT do not reconcile")
        if any(
            row["complete_prefix_bytes"] != row["first_read_limit_bytes"]
            for row in reader.prefixes.values()
        ):
            raise ValueError("truncated workflow events")
        duration = metrics["workflow_event_makespan_ms"] / 1000.
        tokens = plan["source_plan"]["output_tokens"]
        values = {
            "registration": str(path), "registration_sha256": sha256_file(path),
            "application_commit": plan["code_commit"], "arm": str(arm),
            "mean_jct_s": metrics["mean_jct_ms"] / 1000.,
            "workflow_event_makespan_s": duration,
            "end_to_end_output_tokens_per_s": tokens / duration,
            "output_tokens": tokens, "completed_requests": summary["completed_requests"],
            "summary_sha256": sha256_file(summary_path),
            "workflow_events": reader.prefixes,
        }
        if name != CONTROL:
            old = initial["runs"][name]
            if (
                old["registration_sha256"] != values["registration_sha256"]
                or old["summary_sha256"] != values["summary_sha256"]
                or abs(old["mean_jct_s"] - values["mean_jct_s"]) > .001
            ):
                raise ValueError("initial audited inputs changed")
            values["initial_driver_clock_diagnostic"] = {
                "makespan_s": old["makespan_s"],
                "end_to_end_output_tokens_per_s": old["end_to_end_output_tokens_per_s"],
                "extra_driver_interval_s": old["makespan_s"] - duration,
                "not_the_declared_performance_metric": True,
            }
            figure_path = ROOT / "experiments/figures" / name / "evidence.json.gz"
            with gzip.open(figure_path, "rt") as stream:
                figures = json.load(stream)
            matching = [
                row for row in figures["arms"].values()
                if Path(row["arm"]).resolve() == arm.resolve()
            ]
            if len(matching) != 1 or any(
                abs(matching[0][field] - expected) > 1e-6
                for field, expected in (
                    ("mean_jct_s", values["mean_jct_s"]), ("makespan_s", duration),
                    ("end_to_end_output_tokens_per_s", tokens / duration),
                )
            ):
                raise ValueError("established figure metrics do not match")
            values["existing_figure_evidence_sha256"] = sha256_file(figure_path)
        runs[name] = values
    for values in runs.values():
        values["jct_improvement_vs_control_percent"] = 100. * (
            1. - values["mean_jct_s"] / runs[CONTROL]["mean_jct_s"]
        )
    causal.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "complete identical work and workflow-event clock, all normal tails",
        "initial_audit_preserved": str(INITIAL), "initial_audit_sha256": sha256_file(INITIAL),
        "supersedes_only_initial_driver_clock_performance_fields": True,
        "initial_forecast_and_transfer_observations_unchanged": True,
        "runs": runs, "operator_sha256": sha256_file(Path(__file__)),
        "causal_refresh_attribution_verified": False,
        "repeated_performance_accepted": False,
    })
    print(json.dumps({
        name: {key: values[key] for key in (
            "mean_jct_s", "workflow_event_makespan_s",
            "end_to_end_output_tokens_per_s", "jct_improvement_vs_control_percent",
        )} for name, values in runs.items()
    }, indent=2))


if __name__ == "__main__":
    main()
