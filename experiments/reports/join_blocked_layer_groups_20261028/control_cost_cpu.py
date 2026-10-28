"""Measure JOIN handover callbacks with synthetic state and a real async journal."""

import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pytest import MonkeyPatch

from beliefkv.control.causal_graph import InvocationState
from beliefkv.experiments.source_provenance import sha256_file
from beliefkv.runtime.v0520_opportunity_telemetry import NativeOpportunityTelemetry
from tests.test_join_blocked_handoff import blocked, setup
from tests.test_paper_baseline_runtime import request


def main():
    output = Path(__file__).with_suffix(".json")
    if output.exists():
        raise FileExistsError(output)
    results = {}
    for label in ("disabled", "new_handoff", "no_blocked_donor", "backoff"):
        with tempfile.TemporaryDirectory(prefix="bkv-join-cost-") as directory:
            with MonkeyPatch.context() as patch:
                live, adapter, key = setup(
                    patch, Path(directory), capacity=805198, tokens=100000,
                    enabled=label != "disabled",
                )
                writer = NativeOpportunityTelemetry(output.parent / "cpu_journal_verified" / label)
                try:
                    donor = blocked(live, tokens=100000)
                    for index in range(31):
                        req = request("peer-" + str(index), "peer-" + str(index), tokens=21000)
                        live.register_visible_request(req)
                        adapter.policy.programs["peer-" + str(index)].active = True
                    if label == "no_blocked_donor":
                        live.graph.invocations["donor"].state = InvocationState.READY
                    live._opportunity_writer = writer
                    samples = []
                    for index in range(550):
                        adapter.policy.programs["parent"].active = False
                        donor.active = True
                        if adapter._join_handoff is not None:
                            adapter._join_handoff.next_attempt = (
                                time.monotonic() + 1000. if label == "backoff" else 0.
                            )
                        start = time.thread_time_ns()
                        actual = adapter.claim_restore(key, source="join_ticket")
                        elapsed = (time.thread_time_ns() - start) / 1000.
                        if actual is not (label == "new_handoff"):
                            raise ValueError("CPU fixture changed handover outcome")
                        if index >= 50:
                            samples.append(elapsed)
                    results[label] = {
                        "programs": len(adapter.policy.programs),
                        "pending_native_records": len(adapter.requests),
                        "samples": len(samples),
                        "thread_cpu_us": {
                            "median": statistics.median(samples),
                            "p99": sorted(samples)[494], "max": max(samples),
                        },
                    }
                finally:
                    live._opportunity_writer = None
                    live.close()
                    writer.close()
                status = json.loads(writer.status_path.read_bytes())
                if not status["complete"] or status["error"] is not None:
                    raise ValueError("CPU journal did not finish")
                expected = {"agent_join_blocked_capacity_admitted": 550} if label == "new_handoff" else {}
                if status["enqueued"] != expected:
                    raise ValueError("CPU journal count differs from successful handovers")
                results[label]["journal_records"] = sum(status["enqueued"].values())
                results[label]["journal_sha256"] = sha256_file(writer.path)
    sources = (
        Path(__file__), ROOT / "beliefkv/runtime/join_blocked_handoff.py",
        ROOT / "beliefkv/runtime/reference_thunder_admission.py",
        ROOT / "beliefkv/runtime/paper_baseline.py",
        ROOT / "beliefkv/policy/paper_baselines.py",
        ROOT / "tests/test_join_blocked_handoff.py",
    )
    result = {
        "scope": "Callback thread CPU with 33 synthetic programs, 31 pending records and an async journal.",
        "fixtures": results,
        "sources": {str(path.relative_to(ROOT)): sha256_file(path) for path in sources},
        "eligibility_and_retry_reset_for_timing_only": True,
        "writer_thread_cpu_included": False,
        "maximum_64_donor_window_measured": False,
        "whole_controller_cpu_measured": False,
        "gpu_or_jct_gain_measured": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
