"""Consume complete observed replay events through the bound oracle on CPU."""

import argparse
from dataclasses import asdict
import math
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from experiments.reports.arrival_mean120_20261029.queue_pair import complete_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve all earlier progress evidence")
    if complete_work(args.registration) is None:
        raise ValueError("progress qualification requires complete fixed work and actual exit")
    common = causal.load(args.configuration)["common_environment"]
    oracle = WorkflowWorkOracle(common["WORKFLOW_WORK_MODEL"], common["WORKFLOW_WORK_MODEL_SHA256"])
    plan = causal.load(args.registration)
    if set(plan["workflow_ids"]) != oracle.artifact.workflows.keys():
        raise ValueError("observed execution has a different population")
    reader, samples = SealedRecords(), []
    arm = Path(plan["run_root"]) / "full"
    events = reader(arm / "replay/runtime_events.replay.jsonl")
    for index, raw in enumerate(events):
        event = RuntimeEvent.from_dict(raw)
        with patch("beliefkv.runtime.workflow_work_oracle.time.monotonic", return_value=event.ts_ms / 1000.):
            if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
                request = event.attributes["request_id"]
                item = oracle.artifact.requests[request]
                args_call = (
                    request, event.invocation_id, event.context_id, range(item["prompt_tokens"]),
                )
                if event.kind is Kind.LLM_SUBMIT:
                    oracle.submit(event.workflow_id, "started", *args_call)
                else:
                    oracle.submit(
                        event.workflow_id, "completed", *args_call, range(item["output_tokens"]),
                    )
            oracle.submit(event.workflow_id, "event", event)
            if index % 100 == 0 and (prediction := oracle.prediction(event.workflow_id)) is not None:
                if (
                    not math.isfinite(prediction.remaining_path_s)
                    or prediction.remaining_path_s < 0
                    or prediction.remaining_path_s + 1e-7 < prediction.model_only_path_s
                    or prediction.remaining_path_s + 1e-7 < prediction.tool_and_fixed_delay_path_s
                ):
                    raise ValueError("remaining model/tool path is inconsistent")
                samples.append({"workflow_id": event.workflow_id, **asdict(prediction)})
    status = oracle.status()
    counts = status["counts"]
    if (
        status["error"] or any(status["remaining_work_units"].values())
        or counts["completed_requests"] != plan["source_plan"]["requests"]
        or counts["observed_output_tokens"] != plan["source_plan"]["output_tokens"]
        or counts["closed_workflows"] != len(plan["workflow_ids"])
        or counts.get("unmodeled_core_event_observations", 0)
    ):
        raise ValueError("complete observed execution did not conserve oracle progress")
    for path, prefix in reader.prefixes.items():
        if Path(path).stat().st_size != prefix["complete_prefix_bytes"]:
            raise ValueError("completed observed event input changed")
    causal.reservation.write_json(args.output, {
        "artifact_sha256": oracle.digest, "counts": counts,
        "complete_work_conserved": True, "remaining_path_samples": samples,
        "sample_scope": "source-call completion progress, without partial native generation",
        "operator_sha256": sha256_file(Path(__file__)),
        "registration_sha256": sha256_file(args.registration),
        "configuration_sha256": sha256_file(args.configuration),
        "input_jsonl_prefixes": reader.prefixes,
        "future_model_and_tool_information_used": True, "GPU_action": False,
    })
    print({"completed_requests": counts["completed_requests"], "closed_workflows": counts["closed_workflows"],
           "path_samples": len(samples), "path_cpu_ms": counts["path_thread_cpu_us"] / 1000.})


if __name__ == "__main__":
    main()
