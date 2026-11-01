"""Quantify captured prompt rewrites without treating them as KV eviction loss."""

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/.worktrees/beliefkv-final-followup-a013dbd7")
MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
REPORTS = MAIN / "experiments/reports/remaining_original_projects_20261030"
HERE = REPORTS / "compaction_prefix_geometry_complete_controls_r1"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.v0520_native_telemetry import NativeReactiveTelemetry
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(registration, cohort_path):
    verify_execution(registration, cohort_path)
    plan = causal.load(registration)
    measured = set(causal.load(cohort_path)["measured_workflow_ids"])
    arm = Path(plan["run_root"]) / "full"
    reader, last_completed, pending, cases = SealedRecords(), {}, {}, []
    summary_invocations = set()
    for row in reader(arm / "replay/runtime_events.replay.jsonl"):
        kind, owner = row["kind"], row.get("invocation_id")
        attrs = row.get("attributes") or {}
        if kind == "invocation_create" and row.get("agent_definition_id") == "context-summarizer":
            summary_invocations.add(owner)
        elif kind == "llm_result":
            last_completed[owner] = row
        elif kind == "context_compact" and attrs.get("source") == "deepagents_summarization":
            if owner in pending:
                raise ValueError("one caller received overlapping compaction declarations")
            pending[owner] = row, last_completed.get(owner)
        elif kind == "llm_submit" and owner in pending:
            compact, previous = pending.pop(owner)
            if compact["context_id"] != row["context_id"] or (
                previous is not None
                and previous["context_id"] != row["context_id"]
            ):
                raise ValueError("a compaction changed its caller context identity")
            cases.append({
                "workflow_id": row["workflow_id"], "invocation_id": owner,
                "measured_workflow": row["workflow_id"] in measured,
                "compaction_event": compact, "next_request_id": attrs["request_id"],
                "prior_request_id": (
                    previous["attributes"]["request_id"] if previous is not None else None
                ),
            })
    source = Path(plan["source_arm"])
    expected_path = source / "expected.json"
    expected = causal.load(expected_path)
    token_path = source / "server/request_tokens.jsonl"
    with CapturedTokenCorpus(token_path, expected) as corpus:
        if corpus.evidence["token_source_sha256"] != plan["token_source_sha256"]:
            raise ValueError("the exact captured token source changed")
        for case in cases:
            if case["prior_request_id"] is None:
                case["status"] = "prior_complete_caller_request_unobserved"
                continue
            before, after = (
                corpus.get(case[name]) for name in ("prior_request_id", "next_request_id")
            )
            identity = before.identity[:3]
            if identity != after.identity[:3] or identity[:2] != (
                case["workflow_id"], case["invocation_id"],
            ):
                raise ValueError("the compared exact prompts have different owners")
            common = NativeReactiveTelemetry._common_array_prefix(
                before.prompt_tokens, after.prompt_tokens,
            )
            old, new = len(before.prompt_tokens), len(after.prompt_tokens)
            if not 0 <= common <= min(old, new) or old <= 0:
                raise ValueError("invalid exact prompt-prefix geometry")
            case.update(
                status="exact_prompt_pair_compared",
                old_prompt_tokens=old, next_prompt_tokens=new,
                common_input_prefix_tokens=common,
                old_input_suffix_changed_tokens=old - common,
                next_prompt_after_common_prefix_tokens=new - common,
                old_input_prefix_retained_fraction=common / old,
            )
    valid = [row for row in cases if row["status"] == "exact_prompt_pair_compared"]
    groups = {
        "all_observed_compactions": valid,
        "fixed_measured_workflows": [row for row in valid if row["measured_workflow"]],
    }
    result = {
        "registration": str(registration), "complete_measured_execution_verified": True,
        "observed_context_summarizer_invocations": len(summary_invocations),
        "observed_compaction_to_next_caller_request_pairs": len(cases),
        "open_compaction_events_at_background_cutoff": len(pending),
        "status_counts": dict(Counter(row["status"] for row in cases)),
        "groups": {
            name: {
                "pairs": len(rows),
                "old_prompt_tokens": distribution(row["old_prompt_tokens"] for row in rows),
                "old_input_suffix_changed_tokens": distribution(
                    row["old_input_suffix_changed_tokens"] for row in rows),
                "next_prompt_after_common_prefix_tokens": distribution(
                    row["next_prompt_after_common_prefix_tokens"] for row in rows),
                "old_input_prefix_retained_fraction": distribution(
                    row["old_input_prefix_retained_fraction"] for row in rows),
                "retained_at_least_90_percent_pairs": sum(
                    row["old_input_prefix_retained_fraction"] >= .9 for row in rows),
                "retained_below_10_percent_pairs": sum(
                    row["old_input_prefix_retained_fraction"] < .1 for row in rows),
            } for name, rows in groups.items()
        },
        "cases": cases, "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            registration, cohort_path, expected_path, token_path, Path(__file__),
            ROOT / "beliefkv/runtime/v0520_native_telemetry.py",
            ROOT / "beliefkv/experiments/causal_replay_tokens.py",
        )},
        "scope": [
            "Exact captured before/after input-token prefixes, not semantic similarity.",
            "Future captured prompts are used only for offline diagnosis and never scheduling.",
            "Changed input requires new model work and is not eviction-caused old-prefix loss.",
            "Prompt lengths are not physical exclusive HBM/Host occupancy or actual copy bytes.",
            "Changed old input can remain shared or otherwise useful to other invocations.",
            "Early disposal would need an online proof of the retained prefix plus native safety.",
            "No prompt, task, timing, KV state or running experiment is changed.",
        ],
        "GPU_action": False, "performance_accepted": False,
    }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed compaction observations changed")
    return result


def main():
    if (
        HERE.exists()
        or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999"
        or os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve compaction evidence and use hidden-CUDA nice19")
    definitions = {
        "best_original56": (
            OPS / "workflow_original56_initial12_mean70_toolaware_control_20261031_r1.registration.json",
            REPORTS / "sustained56_initial12_mean70_r1/analysis_cohort.json",
        ),
        "final_original82": (
            OPS / "workflow_final82_initial12_mean70_predictive_control_20261031_r1.registration.json",
            REPORTS / "final_oracle_original82_traffic_r1/analysis_cohort.json",
        ),
    }
    for label, values in definitions.items():
        result = inspect(*values)
        write(HERE / (label + ".json"), result)
        print(json.dumps({
            "label": label, "summary_invocations": result["observed_context_summarizer_invocations"],
            "status_counts": result["status_counts"], "groups": result["groups"],
        }), flush=True)
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_sha256": sha256_file(Path(__file__)),
        "outputs_sha256": {
            label: sha256_file(HERE / (label + ".json")) for label in definitions
        },
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
