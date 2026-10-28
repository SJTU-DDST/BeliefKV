"""Observe the owned Full until actual exit without launching GPU work."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    registration = ROOT.parent / "ops/join_blocked_layer_groups_20261028_r1.registration.json"
    plan = causal.load(registration)
    pin = sha256_file(registration)
    arm = Path(plan["run_root"]) / "full"
    output = Path(__file__).with_suffix(".jsonl")
    counts, offsets = Counter(), {}

    def consume(path, category):
        if not path.exists():
            return
        with path.open("rb") as stream:
            offset = offsets.get(path, 0)
            if path.stat().st_size < offset:
                raise ValueError("observed append-only log shrank")
            stream.seek(offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                row = json.loads(line)
                offset += len(line)
                counts[category] += 1
                if category == "events":
                    counts[row["kind"]] += 1
            offsets[path] = offset

    with output.open("x") as log:
        started = time.monotonic()
        while True:
            if sha256_file(registration) != pin:
                raise ValueError("running registration changed")
            state = causal.load(registration.with_suffix(".state.json"))
            if state["status"] == "stopped_for_investigation":
                raise ValueError("Full stopped: " + repr(state.get("error")))
            consume(arm / "replay/native_requests.jsonl", "receipts")
            consume(arm / "replay/runtime_events.replay.jsonl", "events")
            runtime = causal.qualified.last_runtime_state(arm / "opportunities/admission_opportunities.jsonl")
            row = {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "status": state["status"],
                "completed_receipts": counts["receipts"],
                "workflow_ends": counts["workflow_end"],
                "join_satisfied": counts["join_satisfied"],
                "join_budget_handoffs": runtime.get("agent_admission", {}).get("counts", {}).get(
                    "join_blocked_handoff_grants", 0,
                ),
                "layer_handoffs": runtime.get("counts", {}).get("layer_handoff_submitted", 0),
                "layer_handoff_tokens": runtime.get("counts", {}).get("layer_handoff_reused_tokens", 0),
                "physical_disabled": runtime.get("physical_disabled"),
                "not_complete_performance": True,
            }
            log.write(json.dumps(row, allow_nan=False) + "\n")
            log.flush()
            print(json.dumps(row, allow_nan=False), flush=True)
            if state["status"] == "collected_performance_review_pending" and causal.predecessor_ready({
                **plan, "after_registration": str(registration),
            }):
                return
            if time.monotonic() - started > 90 * 60:
                raise TimeoutError("observation deadline reached; do not stop or restart the Full")
            time.sleep(30.)


if __name__ == "__main__":
    main()
