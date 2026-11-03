"""Export R5 labels from a completed natural native capture, without a replay or oracle run."""

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent
from beliefkv.experiments.causal_replay_tokens import CapturedTokenCorpus
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.experiments.workflow_source_work import reconstruct_work
from beliefkv.predictor.workflow_work import R5_SCHEMA
from scripts.register_composite_source import origin_completion
from scripts.run_causal_gpu_replay import source_plan
from scripts.train_workflow_work import (
    NATIVE_PROGRESS, _native_progress_records, native_decode_progress, write,
)
from scripts.train_workflow_work_distribution import snapshots


def capture_workflows(capture, manifest):
    clients = list(capture.glob("client_*/workflows"))
    tasks = {row["instance_id"]: row["repo"] for row in manifest["workloads"]}
    if (
        len(clients) != 1 or len(tasks) != manifest["requested_workflows"]
        or len(set(tasks.values())) != len(tasks)
    ):
        raise ValueError("native training requires one complete project-disjoint task population")
    directories = [path for path in clients[0].iterdir() if path.is_dir()]
    if {path.name for path in directories} != tasks.keys():
        raise ValueError("native training directories differ from the original task population")
    workflows, evidence = [], {}
    for directory in sorted(directories):
        result_path, event_path = directory / "result.json", directory / "runtime_events.deepagents.jsonl"
        result = json.loads(result_path.read_bytes())
        control = result.get("agent_control") or {}
        if (
            result.get("natural_terminal") is not True or result.get("error")
            or result.get("semantic_gate_controlled_stop")
            or any(control.get(name, 0) for name in (
                "guard_intervened_completions", "guard_intervened_natural_language_returns",
                "forced_semantic_completions",
            ))
        ):
            raise ValueError("new training tasks must keep every complete natural return")
        events = [RuntimeEvent.from_dict(json.loads(line)) for line in event_path.open()]
        identities = {event.workflow_id for event in events}
        if len(identities) != 1:
            raise ValueError("a native training task changed workflow ownership")
        workflows.append({
            "project": tasks[directory.name], "task_id": directory.name,
            "workflow_id": next(iter(identities)), "events": events,
            "event_sha256": sha256_file(event_path),
        })
        evidence.update({str(path.resolve()): sha256_file(path) for path in (result_path, event_path)})
    if len({item["workflow_id"] for item in workflows}) != len(workflows):
        raise ValueError("native training tasks contain a duplicate workflow")
    return workflows, evidence


def export_native(registration):
    registration = registration.resolve()
    plan, completion = origin_completion(registration, {})
    capture = Path(plan["run_root"]) / "native_capture"
    manifest_path = Path(plan["environment"]["WORKLOAD_MANIFEST"])
    manifest = json.loads(manifest_path.read_bytes())
    workflows, evidence = capture_workflows(capture, manifest)
    if len(workflows) != int(plan["environment"]["ROOT_COUNT"]):
        raise ValueError("the native training task count differs from its registered collection")
    graph, expected, graph_evidence = source_plan(capture)
    tokens = capture / "server/request_tokens.jsonl"
    with CapturedTokenCorpus(tokens, expected) as corpus:
        requests, totals = reconstruct_work(graph, corpus)
    if {item["workflow_id"] for item in workflows} != totals.keys():
        raise ValueError("natural training events and exact native token work differ")
    service = capture / "server/runtime_audit.jsonl"
    observations = defaultdict(list)
    for observation in native_decode_progress(
        (event for item in workflows for event in item["events"]),
        _native_progress_records(service),
        {request: value["output_tokens"] for request, value in requests.items()},
    ):
        observations[observation["workflow_id"]].append(observation)
    rows = []
    for item in workflows:
        identity = item["workflow_id"]
        values = snapshots(item["events"], requests, progress=observations[identity])
        if not math.isclose(values[-1]["remaining_work"], 0., abs_tol=1e-6):
            raise ValueError("the complete native training execution did not conserve final work")
        for value in values:
            rows.append({
                **value, "workflow": str(capture.resolve()) + ":" + identity,
                "logical_workflow_id": identity, "source_capture": str(capture.resolve()),
                "source_workflow_sha256": item["event_sha256"], "project": item["project"],
                "task_template": hashlib.sha256(values[0]["text"].encode()).hexdigest(),
            })
    evidence.update(completion)
    evidence.update({str(path.resolve()): sha256_file(path) for path in (
        registration, registration.with_suffix(".state.json"), manifest_path, tokens, service,
    )})
    for path, prefix in graph_evidence["input_jsonl_prefixes"].items():
        if Path(path).stat().st_size != prefix["complete_prefix_bytes"]:
            raise ValueError("native training source has an unfinished or changed JSONL suffix")
        evidence[path] = prefix["complete_prefix_sha256"]
    if any(sha256_file(Path(path)) != digest for path, digest in evidence.items()):
        raise ValueError("a completed native training source changed during export")
    return {
        "schema": R5_SCHEMA, "rows": rows, "sources_sha256": evidence,
        "prefill_token_weight": .01, "progress_observation_contract": NATIVE_PROGRESS,
        "future_source_graph_used_for_labels_only": True,
        "future_tool_waits_excluded_from_model_work_target": True,
        "full_prompt_and_tool_outputs_excluded_from_features": True,
        "complete_original_workflows": len(workflows), "observed_delegation_inputs": False,
        "native_training_capture": {
            "registration": str(registration), "code_commit": plan["code_commit"],
            "source_requests": len(requests), "original_tasks_preserved": True,
            "all_natural_tails_fresh_drain_and_actual_owned_exit_verified": True,
            "tool_duration_overlay_used": False, "GPU_action": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("preserve previously exported natural training data")
    values = export_native(args.registration)
    write(args.output, values)
    print(json.dumps({
        "workflows": values["complete_original_workflows"], "snapshots": len(values["rows"]),
        "output": str(args.output), "GPU_action": False,
    }), flush=True)


if __name__ == "__main__":
    main()
