"""Use the qualified fast resource supervisor for one frozen materialization arm."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

HELPER = Path("/home/longhao/experiment/.worktrees/beliefkv-fast-resource-wait-20261115")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def watch_shared(registration, expected_sha256):
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999":
        raise ValueError("the CPU supervisor must hide CUDA")
    plan = json.loads(registration.read_bytes())
    root = Path(plan["execution_checkout"])
    sys.path.insert(0, str(root))
    from scripts import run_causal_comparison as causal

    if causal.ROOT != root or causal.sha256_file(registration) != expected_sha256:
        raise ValueError("shared supervision requires the unchanged frozen registration")
    helper = load_module("materialization_fast_wait", HELPER / "beliefkv/experiments/fast_gpu_wait.py")
    device = helper.NvmlDevice()
    original_wait = causal.wait_for_resources
    try:
        causal.wait_for_resources = helper.FastWait(
            device.snapshot, lambda port: helper.machine_resources(port, root, causal.reservation.owned_runs),
        )
        causal.watch(registration)
    finally:
        causal.wait_for_resources = original_wait
        device.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    preparation = json.loads((args.evidence_dir / "preparation.json").read_bytes())
    if preparation.get("shared_gpu"):
        watch_shared(args.watch.resolve(), preparation["registration_sha256"])
        return
    module = load_module("materialization_fast_supervisor", Path(preparation["fast_supervisor"]))
    module.HERE = args.evidence_dir.resolve()
    module.watch(args.watch.resolve())


if __name__ == "__main__":
    main()
