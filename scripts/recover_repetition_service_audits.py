"""Resume CPU service audits from byte-identical frozen readers after a development-source change."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT.parent / "ops"


def digest(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def frozen_bindings(pins, development, frozen):
    mapped, bindings = {}, []
    for name, expected in pins.items():
        path = Path(name)
        relative = path.relative_to(development) if path.is_relative_to(development) else None
        source = relative is not None and relative.parts[0] in ("scripts", "beliefkv")
        target = frozen / relative if source else path
        if not target.is_file() or digest(target) != expected:
            raise ValueError("the exact prior source or input is unavailable: " + str(target))
        mapped[str(target.resolve())] = expected
        if source:
            bindings.append({
                "original": str(path), "frozen": str(target.resolve()), "sha256": expected,
                "byte_identical_to_prior_registered_reader": True,
            })
    return mapped, bindings


def require_stopped(state, alive):
    if (
        state.get("status") != "stopped_preserve_repeated_service_audits"
        or not state.get("error", "").startswith("ValueError('campaign input changed:")
        or state.get("completed_methods") != []
        or not state.get("process") or alive(state["process"])
    ):
        raise ValueError("recover only an actually exited source-change failure before any service audit")


def observer():
    sys.path.insert(0, str(OPS))
    import finish_background83_replication_service_20261107 as source

    return source


def require_cpu():
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "99999" or os.getpriority(os.PRIO_PROCESS, 0) != 19:
        raise ValueError("service audit recovery requires hidden CUDA and nice19")


def register(previous_plan, frozen, output):
    require_cpu()
    source = observer()
    previous_plan, frozen, output = (
        path.resolve() for path in (previous_plan, frozen, output)
    )
    old, state_path = source.repeat.load(previous_plan), previous_plan.parent / "state.json"
    state = source.repeat.load(state_path)
    require_stopped(state, source.repeat.causal.reservation.alive)
    if output.exists():
        raise FileExistsError("preserve previous recovery registrations")
    pins, bindings = frozen_bindings(old["inputs_sha256"], ROOT, frozen)
    if not source.repeat.causal.reservation.alive(old["parent_process"]):
        raise ValueError("the exact repetition coordinator must still be live at recovery registration")
    plan = deepcopy(old)
    plan.update(
        registered_at=datetime.now(timezone.utc).isoformat(), analysis_root=str(frozen),
        preserved_failed_plan=str(previous_plan), preserved_failed_state=str(state_path),
        preserved_failure=state, frozen_reader_bindings=bindings,
        analysis_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=frozen, text=True,
        ).strip(),
        recovery_script=str(Path(__file__).resolve()),
        inputs_sha256={
            **pins, str(previous_plan): digest(previous_plan), str(state_path): digest(state_path),
            str(Path(__file__).resolve()): digest(Path(__file__)),
        },
        same_registered_traffic_resources_metrics_and_service_checks=True,
        no_GPU_queue_or_serving_changes=True, GPU_action=False,
    )
    output.mkdir(parents=True, exist_ok=False)
    plan_path = output / "plan.json"
    source.repeat.write(plan_path, plan)
    with (output / "watch.log").open("x") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watch", str(plan_path)],
            cwd=frozen, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999",
                             "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                             "MKL_NUM_THREADS": "1"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "plan": str(plan_path), "process": source.repeat.causal.reservation.process(child.pid),
        "byte_identical_reader_count": len(bindings), "GPU_action": False,
    }), flush=True)


def watch(plan_path):
    require_cpu()
    source = observer()
    plan_path = plan_path.resolve()
    plan = source.repeat.load(plan_path)
    source.MAIN, source.HERE, source.PLAN = Path(plan["analysis_root"]), plan_path.parent, plan_path
    source.watch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=Path)
    parser.add_argument("--previous-plan", type=Path)
    parser.add_argument("--analysis-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.watch is not None:
        if any(value is not None for value in (args.previous_plan, args.analysis_root, args.output_dir)):
            parser.error("watch a single registered recovery plan")
        watch(args.watch)
    elif any(value is None for value in (args.previous_plan, args.analysis_root, args.output_dir)):
        parser.error("registration requires the failed plan, frozen analysis root and a new output directory")
    else:
        register(args.previous_plan, args.analysis_root, args.output_dir)


if __name__ == "__main__":
    main()
