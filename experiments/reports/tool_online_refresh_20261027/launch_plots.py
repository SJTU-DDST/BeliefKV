"""Queue CPU-only plots behind this exact Full and its completed control."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts import run_causal_comparison as causal


def main():
    ops = Path("/home/longhao/experiment/ops")
    name = "tool_online_refresh_20261027_r1"
    registration = ops / (name + ".registration.json")
    control = ops / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
    launch = ops / (name + ".plots.launch.json")
    state = ops / (name + ".plots.state.json")
    log_path = ops / (name + ".plots.log")
    if any(path.exists() for path in (launch, state, log_path)):
        raise FileExistsError("preserve the existing CPU plot waiter")
    plan = causal.load(registration)
    checkout = Path(plan["execution_checkout"])
    operator = checkout / "scripts/deferred_causal_decode_plots.py"
    original = subprocess.check_output([
        "git", "-C", str(checkout), "show",
        plan["code_commit"] + ":" + str(operator.relative_to(checkout)),
    ])
    if sha256_file(operator) != hashlib.sha256(original).hexdigest():
        raise ValueError("CPU plotting operator differs from the frozen execution")
    current = causal.load(registration.with_suffix(".state.json"))
    if current.get("status") != "running_full" or not causal.reservation.alive(current["watcher"]):
        raise ValueError("Full supervisor is not actually live")
    command = [
        "nice", "-n", "19", "ionice", "-c", "3", sys.executable, str(operator),
        "--full-registration", str(registration), "--control-registration", str(control),
        "--output-dir", str(ROOT / "experiments/figures" / name),
        "--state", str(state),
        "--plot-python", str(ops / "cpu_plotting_20261027/bin/python"),
    ]
    with log_path.open("x") as log:
        child = subprocess.Popen(
            command, cwd=checkout,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    result = {
        "command": command, "process": causal.reservation.process(child.pid),
        "registration_sha256": sha256_file(registration),
        "control_sha256": sha256_file(control), "operator_sha256": sha256_file(operator),
        "launcher_sha256": sha256_file(Path(__file__)),
        "gpu_work_authorized": False, "performance_accepted": False,
    }
    causal.reservation.write_json(launch, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
