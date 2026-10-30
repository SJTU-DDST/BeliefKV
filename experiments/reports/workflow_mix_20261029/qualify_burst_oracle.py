"""Qualify cached oracle progress against the complete new Full event history."""

import argparse
from dataclasses import asdict
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.core.events import RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.workflow_work_oracle import WorkflowWorkOracle
from experiments.reports.workflow_mix_20261029.review_burst import complete_work
from scripts.audit_sealed_wait_prefetch import SealedRecords
from scripts import run_causal_comparison as causal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or args.output.exists():
        raise ValueError("hide CUDA and preserve previous oracle evidence")
    if complete_work(args.control) is None:
        raise ValueError("wait for complete control work and actual exit")
    specification = causal.load(args.configuration)
    common = specification["common_environment"]
    oracle = WorkflowWorkOracle(common["WORKFLOW_WORK_MODEL"], common["WORKFLOW_WORK_MODEL_SHA256"])
    plan = causal.load(args.control)
    if set(plan["workflow_ids"]) != oracle.artifact.workflows.keys():
        raise ValueError("control and oracle workflow populations differ")
    reader, samples = SealedRecords(), []
    arm = Path(plan["run_root"]) / "full"
    for index, raw in enumerate(reader(arm / "replay/runtime_events.replay.jsonl")):
        event = RuntimeEvent.from_dict(raw)
        with patch("beliefkv.runtime.workflow_work_oracle.time.monotonic", return_value=event.ts_ms / 1000.):
            if event.kind in (Kind.LLM_SUBMIT, Kind.LLM_RESULT):
                request = event.attributes["request_id"]
                item = oracle.artifact.requests[request]
                arguments = request, event.invocation_id, event.context_id, range(item["prompt_tokens"])
                if event.kind is Kind.LLM_SUBMIT:
                    oracle.submit(event.workflow_id, "started", *arguments)
                else:
                    oracle.submit(
                        event.workflow_id, "completed", *arguments, range(item["output_tokens"]),
                    )
            oracle.submit(event.workflow_id, "event", event)
            if index % 100 == 0:
                prediction = oracle.prediction(event.workflow_id)
                if prediction is not None:
                    if (
                        not math.isfinite(prediction.remaining_path_s)
                        or prediction.remaining_path_s < 0
                        or prediction.remaining_path_s + 1e-7 < prediction.model_only_path_s
                        or prediction.remaining_path_s + 1e-7 < prediction.tool_and_fixed_delay_path_s
                    ):
                        raise ValueError("oracle model/tool remaining path is inconsistent")
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
        raise ValueError("complete observed control progress did not conserve oracle work")
    for path, prefix in reader.prefixes.items():
        if (
            Path(path).stat().st_size != prefix["complete_prefix_bytes"]
            or sha256_file(Path(path)) != prefix["complete_prefix_sha256"]
        ):
            raise ValueError("completed control event input changed")
    causal.reservation.write_json(args.output, {
        "artifact_sha256": oracle.digest, "counts": counts, "complete_work_conserved": True,
        "remaining_path_samples": samples,
        "sample_scope": "complete_call_boundaries_without_claimed_native_partial_callback_reconstruction",
        "operator_sha256": sha256_file(Path(__file__)),
        "control_registration": str(args.control.resolve()),
        "control_registration_sha256": sha256_file(args.control),
        "configuration_sha256": sha256_file(args.configuration),
        "input_jsonl_prefixes": reader.prefixes,
        "future_information_used": True, "deployable": False, "GPU_action": False,
    })
    print({
        "completed_requests": counts["completed_requests"], "closed_workflows": counts["closed_workflows"],
        "path_samples": len(samples), "path_cpu_ms": counts["path_thread_cpu_us"] / 1000.,
    })


if __name__ == "__main__":
    main()
