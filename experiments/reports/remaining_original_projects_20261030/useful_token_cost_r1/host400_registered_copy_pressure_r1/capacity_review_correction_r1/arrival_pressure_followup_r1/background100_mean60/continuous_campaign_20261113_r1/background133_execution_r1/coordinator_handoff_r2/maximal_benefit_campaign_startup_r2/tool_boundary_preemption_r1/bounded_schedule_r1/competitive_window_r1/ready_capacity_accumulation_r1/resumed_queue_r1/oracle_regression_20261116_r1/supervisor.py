"""Wait on the existing campaign using CPU only, then use the fast supervisor."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("strict_Oracle_queue", HERE / "strict_oracle_queue.py")
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path, required=True)
    args = parser.parse_args()
    plan = queue.load(queue.QUEUE / "plan.json")
    state = {
        "status": "waiting_existing_materialization_campaign",
        "process": queue.causal.reservation.process(os.getpid()),
        "GPU_initialized": False, "GPU_model_started": False,
    }
    while not queue.predecessor_ready(plan):
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        queue.write(queue.QUEUE / "supervisor_wait.json", state)
        time.sleep(.5)
    queue.verify_pins(plan["inputs_sha256"])
    spec = importlib.util.spec_from_file_location(
        "strict_fast_supervisor", Path(plan["fast_supervisor"]),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = queue.QUEUE
    device = module.helper.NvmlDevice()
    try:
        wait = module.helper.FastWait(
            device.snapshot,
            lambda port: module.helper.machine_resources(
                port, queue.ROOT, queue.causal.reservation.owned_runs,
            ),
        )
        wait(
            plan["stages"]["oracle"], state,
            pulse=lambda: queue.write(queue.QUEUE / "supervisor_wait.json", state),
        )
    finally:
        device.close()
    state.update(status="actual_resources_ready_starting_frozen_Oracle")
    queue.write(queue.QUEUE / "supervisor_wait.json", state)
    # Avoid caching development modules before the supervisor selects the frozen tree.
    for name in tuple(sys.modules):
        if name == "scripts" or name.startswith(("scripts.", "beliefkv.")) or name == "beliefkv":
            del sys.modules[name]
    module.watch(args.watch.resolve())


if __name__ == "__main__":
    main()
