"""Measure all observed dirty-tail copies, independently of later eviction."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path("/home/longhao/experiment/BeliefKV")
REPORTS = ROOT / "experiments/reports/remaining_original_projects_20261030"
PRIOR = REPORTS / "handoff_tagged_copies_complete_controls_r2"
HERE = REPORTS / "handoff_all_tagged_backup_copies_r3"
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts.train_workflow_work import write


def inspect(label):
    previous_path = PRIOR / (label + ".json")
    previous = causal.load(previous_path)
    registration = Path(previous["registration"])
    plan = causal.load(registration)
    transfer_path = Path(plan["run_root"]) / "full/server/transfer_telemetry.jsonl"
    transfers, children, reader = {}, {}, SealedRecords()
    for row in reader(transfer_path):
        if row["direction"] != "d2h":
            continue
        transfers[row["command_id"]] = row
        for child in row["tagged_child_commits"]:
            if child["command_id"] in children:
                raise ValueError("a shadow command has more than one final native receipt")
            children[child["command_id"]] = row["command_id"], child
    backups = [
        (row["command_ids"], row["actual_tagged_D2H_tokens"]) for row in previous["episodes"]
    ] + [(row["command_ids"], row["backup_tokens_budgeted"]) for row in previous["unmatched"]]
    if len(backups) != previous["original_unbacked_backup_episodes"]:
        raise ValueError("complete original backup population was not retained")
    matched, missing, native_ids, tagged_bytes = 0, [], set(), 0
    for commands, budget in backups:
        if any(command not in children for command in commands):
            missing.append(commands)
            continue
        if sum(children[command][1]["num_tokens_by_pool"].get("kv", 0) for command in commands) != budget:
            raise ValueError("actual tagged tokens differ from the dirty-tail budget")
        matched += 1
        native_ids.update(children[command][0] for command in commands)
        tagged_bytes += sum(children[command][1]["num_bytes"] for command in commands)
    prefix = reader.prefixes[str(transfer_path)]
    if prefix != previous["input_jsonl_prefixes"][str(transfer_path)] or (
        sha256_file(transfer_path) != prefix["complete_prefix_sha256"]
    ):
        raise ValueError("completed native transfer evidence changed")
    result = {
        "label": label, "all_predicted_dirty_tail_backup_episodes": len(backups),
        "actual_D2H_tagged_episodes": matched, "unknown_D2H_commands": missing,
        "actual_tagged_bytes": tagged_bytes,
        "deduplicated_containing_native_D2H_stream_ms": sum(
            transfers[key]["transfer_stream_elapsed_ms"] for key in native_ids),
        "containing_native_copy_submit_to_ACK_ms": sum(
            transfers[key]["submit_to_ack_ms"] for key in native_ids),
        "strict_following_release_episodes": previous["tagged_and_following_eviction_matched_episodes"],
        "inputs_sha256": {str(path): sha256_file(path) for path in (previous_path, registration, Path(__file__))},
        "input_jsonl_prefixes": reader.prefixes,
        "scope": [
            "All backup commands are checked, without requiring a later same-consumer eviction.",
            "Containing native transfers are deduplicated and may include unrelated coalesced work.",
            "This is donor copy service scale, not exposed wait, JCT or an upper bound on queueing effects.",
            "Earlier audits and incomplete release matches remain unchanged.",
        ],
        "GPU_action": False,
    }
    write(HERE / (label + ".json"), result)
    print(json.dumps(result), flush=True)
    return result


def main():
    if HERE.exists() or os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or (
        os.getpriority(os.PRIO_PROCESS, 0) != 19
    ):
        raise ValueError("preserve prior evidence and use nice19 hidden-CUDA analysis")
    write(HERE / "audit.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "arms": {label: inspect(label) for label in ("best_original56", "latest_original69")},
        "GPU_action": False,
    })


if __name__ == "__main__":
    main()
