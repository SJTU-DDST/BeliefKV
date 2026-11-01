"""Bind predicted dirty-tail backups to authoritative native tagged ACKs."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
PRIOR = REPORTS / "handoff_service_scale_complete_controls_r1"
HERE = REPORTS / "handoff_tagged_copies_complete_controls_r2"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_native_system_costs import distribution
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write
from scripts.verify_measured_execution import verify_execution


def inspect(label):
    previous_path = PRIOR / (label + ".json")
    previous = causal.load(previous_path)
    for filename, digest in previous["inputs_sha256"].items():
        if sha256_file(Path(filename)) != digest:
            raise ValueError("earlier complete audit input changed")
    for filename, prefix in previous["input_jsonl_prefixes"].items():
        if sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]:
            raise ValueError("earlier sealed raw evidence changed")
    registration = Path(previous["registration"])
    cohort_paths = [
        Path(filename) for filename in previous["inputs_sha256"]
        if Path(filename).name == "analysis_cohort.json"
    ]
    if len(cohort_paths) != 1:
        raise ValueError("one fixed measurement definition is required")
    execution = verify_execution(registration, cohort_paths[0])
    plan = causal.load(registration)
    arm = Path(plan["run_root"]) / "full"
    reader, command_copies, copies = SealedRecords(), defaultdict(list), {}
    for row in reader(arm / "server/transfer_telemetry.jsonl"):
        if row["direction"] != "d2h":
            continue
        if row["status"] != "completed" or row["command_id"] in copies:
            raise ValueError("require unique completed native D2H ACKs")
        copies[row["command_id"]] = row
        for child in row["tagged_child_commits"]:
            if not set(child["published_node_ids"]) <= set(row["node_ids"]):
                raise ValueError("tagged child points outside its authoritative native ACK")
            command_copies[child["command_id"]].append((row["command_id"], child))
    events, latest, episodes, unmatched = [], {}, [], []
    for row in reader(arm / "opportunities/admission_opportunities.jsonl"):
        if row.get("event") in ("predicted_tool_offload_backup", "predicted_tool_kv_evicted"):
            events.append(row)
    used = set()
    for row in sorted(events, key=lambda item: item["ts_ms"]):
        key = row["context_id"], row["context_epoch"], row.get("beneficiary_request_id")
        if row["event"] == "predicted_tool_offload_backup":
            if key in latest:
                unmatched.append(latest[key])
            latest[key] = row
            continue
        backup = latest.pop(key, None)
        if backup is None:
            continue
        children = [pair for command in backup["command_ids"]
                    for pair in command_copies.get(command, ())]
        if not children:
            unmatched.append(backup)
            continue
        ids = sorted({native for native, _child in children})
        actual_tokens = sum(child["num_tokens_by_pool"].get("kv", 0) for _native, child in children)
        actual_bytes = sum(child["num_bytes"] for _native, child in children)
        if actual_tokens != backup["backup_tokens_budgeted"]:
            raise ValueError("tagged actual D2H tokens differ from the issued dirty-tail budget")
        ack = max(copies[identity]["complete_ts_ms"] for identity in ids)
        if ack > row["ts_ms"] + .001:
            raise ValueError("physical donor release precedes its authoritative native ACK")
        used.update(ids)
        episodes.append({
            "context_id": key[0], "context_epoch": key[1], "beneficiary_request_id": key[2],
            "command_ids": backup["command_ids"], "native_copy_ids": ids,
            "actual_tagged_D2H_tokens": actual_tokens, "actual_tagged_D2H_bytes": actual_bytes,
            "whole_native_copy_stream_ms": sum(copies[item]["transfer_stream_elapsed_ms"] for item in ids),
            "whole_native_copy_submit_to_ACK_ms": sum(copies[item]["submit_to_ack_ms"] for item in ids),
            "backup_event_to_ACK_ms": max(0., ack - backup["ts_ms"]),
            "ACK_to_observed_eviction_ms": row["ts_ms"] - ack,
            "backup_to_observed_eviction_ms": row["ts_ms"] - backup["ts_ms"],
            "consumer_restore_budget_bytes": backup["beneficiary_restore_bytes_budgeted"],
        })
    unmatched.extend(latest.values())
    values = {
        "actual_tagged_D2H_tokens": distribution(row["actual_tagged_D2H_tokens"] for row in episodes),
        "whole_native_copy_stream_ms_per_episode": distribution(row["whole_native_copy_stream_ms"] for row in episodes),
        "whole_native_copy_submit_to_ACK_ms_per_episode": distribution(
            row["whole_native_copy_submit_to_ACK_ms"] for row in episodes),
        "backup_event_to_ACK_ms": distribution(row["backup_event_to_ACK_ms"] for row in episodes),
        "ACK_to_observed_eviction_ms": distribution(row["ACK_to_observed_eviction_ms"] for row in episodes),
        "backup_to_observed_eviction_ms": distribution(row["backup_to_observed_eviction_ms"] for row in episodes),
    }
    result = {
        "registration": str(registration), "measured_requests": execution["measured_requests"],
        "original_unbacked_backup_episodes": previous["unbacked_predicted_backup_episodes"],
        "tagged_and_following_eviction_matched_episodes": len(episodes),
        "unmatched_backup_episodes": len(unmatched),
        "deduplicated_matched_native_D2H_stream_ms": sum(
            copies[item]["transfer_stream_elapsed_ms"] for item in used),
        "deduplicated_matched_native_D2H_actual_bytes": sum(copies[item]["actual_bytes"] for item in used),
        **values, "episodes": episodes, "unmatched": unmatched,
        "input_jsonl_prefixes": reader.prefixes,
        "inputs_sha256": {str(path): sha256_file(path) for path in (
            previous_path, registration, cohort_paths[0], Path(execution["certificate"]), Path(__file__),
        )},
        "scope": [
            "Native tagged_child_commits bind the Full shadow command to its actual ACK.",
            "A whole coalesced native copy may contain other children; its stream cost is conservative.",
            "All completed background copies remain included; no JCT attribution or upper bound is claimed.",
            "ACK-to-release is observed event delay, not proven idle GPU or fully removable time.",
            "R1 direct top-level command matching was incomplete; R1 is retained unchanged.",
        ],
        "GPU_action": False,
    }
    for filename, prefix in reader.prefixes.items():
        if Path(filename).stat().st_size != prefix["complete_prefix_bytes"] or (
            sha256_file(Path(filename)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed evidence changed while being read")
    write(HERE / (label + ".json"), result)
    print(json.dumps({key: result[key] for key in (
        "registration", "tagged_and_following_eviction_matched_episodes", "unmatched_backup_episodes",
        "deduplicated_matched_native_D2H_stream_ms", "actual_tagged_D2H_tokens",
        "backup_event_to_ACK_ms", "ACK_to_observed_eviction_ms",
    )}), flush=True)
    return result


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve prior audits and use nice19 CPU-only settings")
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": {label: inspect(label) for label in ("best_original56", "latest_original69")},
        "GPU_action": False, "performance_accepted": False,
    })


if __name__ == "__main__":
    main()
