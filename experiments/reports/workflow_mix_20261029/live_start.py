"""Read-only verification of the actually running mixed-scope native source."""

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.predictor.workflow_rank_input import WorkflowRankInput
from scripts import run_causal_source_capture as capture
from scripts.audit_sealed_wait_prefetch import SealedRecords


def main():
    registration = Path("/home/longhao/experiment/ops/workflow_mix_scope18_source_20261029_r1.registration.json")
    output = ROOT / "experiments/reports/workflow_mix_20261029/live_start_r1.json"
    if output.exists():
        raise FileExistsError("preserve the earlier live-start receipt")
    plan = capture.load(registration)
    state = capture.load(registration.with_suffix(".state.json"))
    capture.comparison.verify_sources(plan)
    if state["status"] != "recording_native" or any(
        not capture.reservation.alive(state[name]) for name in ("watcher", "driver")
    ):
        raise ValueError("require the actually running source collector")
    run = Path(plan["run_root"]) / "native_capture"
    server = capture.load(run / "server/server_info.json")
    actual = {name: server.get(name) for name in (
        "chunked_prefill_size", "enable_mixed_chunk", "max_running_requests",
        "random_seed", "enable_beliefkv_admission",
    )}
    if actual != {
        "chunked_prefill_size": 2048, "enable_mixed_chunk": False,
        "max_running_requests": 24, "random_seed": 21, "enable_beliefkv_admission": False,
    }:
        raise ValueError("actual native serving parameters differ from the declaration")
    sampler = state["sampling_kernel_preflight"]
    if (
        sampler.get("gpu_sampling_verified") is not True
        or sampler.get("native_kernel_verified") is not True
        or {item["batch_size"] for item in sampler["cases"]} != {1, 8, 24}
        or sha256_file(Path(sampler["path"])) != sampler["sha256"]
    ):
        raise ValueError("fresh native CUDA/sampler checks did not pass")
    gpu_pids = [
        int(line) for line in subprocess.check_output([
            "nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits",
        ], text=True).splitlines() if line.strip()
    ]
    if not gpu_pids:
        raise ValueError("no actual native GPU owner")
    processes = {name: capture.reservation.process(state[name]["pid"]) for name in ("watcher", "driver")}
    for gpu in gpu_pids:
        owner, lineage = gpu, []
        while owner > 1:
            process = capture.reservation.process(owner)
            if process is None:
                raise ValueError("native GPU process disappeared during inspection")
            lineage.append(process)
            owner = process["ppid"]
        if state["driver"]["pid"] not in {item["pid"] for item in lineage}:
            raise ValueError("another job owns GPU memory")
        processes["scheduler"] = lineage[0]
        launchers = [item for item in lineage if "sglang.launch_server" in item["argv"]]
        if len(launchers) != 1:
            raise ValueError("native GPU owner lacks its actual server launcher")
        processes["server"] = launchers[0]
    if any(os.getpriority(os.PRIO_PROCESS, item["pid"]) != 0 for item in processes.values()):
        raise ValueError("measured native processes must remain nice0")
    clients = list(run.glob("client_*/manifest.json"))
    if len(clients) != 1:
        raise ValueError("source lacks one live client manifest")
    manifest = capture.load(clients[0])
    workload = capture.load(Path(plan["environment"]["WORKLOAD_MANIFEST"]))
    if (
        len({row["repo"] for row in workload["workloads"]}) != 18
        or set(manifest["instance_ids"]) != {row["instance_id"] for row in workload["workloads"]}
    ):
        raise ValueError("live tasks differ from the eighteen original projects")
    reader = SealedRecords()
    tokens = Counter(row["event"] for row in reader(run / "server/request_tokens.jsonl"))
    events, semantics = Counter(), Counter()
    for path in clients[0].parent.glob("workflows/*/runtime_events.deepagents.jsonl"):
        for event in reader(path):
            events[event["kind"]] += 1
            value = event.get("attributes", {}).get("workflow_ranking_input")
            if value is not None:
                item = WorkflowRankInput.from_dict(value)
                semantics[event["kind"]] += 1
                if event["kind"] == "workflow_start" and not item.task_instructions:
                    raise ValueError("new root did not expose its explicit task metadata")
    if events["workflow_start"] != 18 or tokens["request_output"] <= 0 or semantics["workflow_start"] != 18:
        raise ValueError("require actual complete outputs and all eighteen root semantic boundaries")
    finisher = capture.load(ROOT / "experiments/reports/workflow_mix_20261029/scope18_finish_r1.registration.json")
    process = capture.reservation.process(finisher["process"]["pid"])
    if process is None or os.getpriority(os.PRIO_PROCESS, process["pid"]) != 19:
        raise ValueError("CPU postprocessor is not alive at nice19")
    environment = Path(f"/proc/{process['pid']}/environ").read_bytes().split(b"\0")
    if b"CUDA_VISIBLE_DEVICES=99999" not in environment:
        raise ValueError("CPU finisher must not acquire a GPU")
    capture.reservation.write_json(output, {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "registration": str(registration), "registration_sha256": sha256_file(registration),
        "code_commit": plan["code_commit"], "run_root": str(run), "processes": processes,
        "all_owned_processes_nice0": True, "sole_GPU_owner_verified": True,
        "actual_server_parameters": actual, "fresh_sampler_cases": [1, 8, 24],
        "token_receipt_counts": dict(tokens), "event_counts": dict(events),
        "semantic_metadata_counts": dict(semantics), "workflow_count": 18,
        "frozen_sources_verified": True, "input_jsonl_prefixes": reader.prefixes,
        "CPU_finisher": {"process": process, "nice": 19, "CUDA_VISIBLE_DEVICES": "99999"},
        "source_capture_complete": False, "measured_size_mix_qualified": False,
        "new_followup_GPU_action_registered": False, "performance_accepted": False,
        "operator_sha256": sha256_file(Path(__file__)),
    })
    print({
        "status": "recording_native", "workflows": 18,
        "completed_output_receipts": tokens["request_output"], "semantic_boundaries": dict(semantics),
    })


if __name__ == "__main__":
    main()
