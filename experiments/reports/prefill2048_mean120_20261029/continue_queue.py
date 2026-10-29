"""Replace only the failed CPU monitor while preserving the live GPU control."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.prefill2048_mean120_20261029 import queue_pair as queue
from scripts import run_causal_comparison as causal
from scripts.qualify_free_page_h2d_fence import check_reservation


def validate_original(plan, state, comparison, comparison_state, *, full_registered, old_watcher_alive):
    label = queue.ORDER[0]
    if (
        state["status"] != "stopped_no_next_gpu_action"
        or state.get("error") != "ValueError('position supervisor exited without registering its full comparison')"
        or old_watcher_alive or full_registered
        or plan["order"] != list(queue.ORDER)
        or state.get("current_arm") != label
        or state.get("completed_arms") != []
        or comparison["order"] != [label]
        or comparison["source_server_parameters"]["chunked_prefill_size"] != 2048
        or comparison["source_server_parameters"]["enable_mixed_chunk"] is not False
        or comparison_state["status"] not in (
            "waiting_resources_" + label, "starting_" + label, "running_" + label,
            "collected_performance_review_pending",
        )
    ):
        raise ValueError("adopt only this monitor failure and its unchanged registered ThunderAgent arm")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-plan", type=Path, required=True)
    parser.add_argument("--operator-evidence", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_dir.exists() or os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("require clean frozen sources, fresh output and nice0")
    authorization = check_reservation()
    old = causal.load(args.original_plan)
    old_state_path = args.original_plan.with_suffix(".state.json")
    old_state = causal.load(old_state_path)
    control_path = Path(old["entries"][queue.ORDER[0]]["comparison_registration"])
    control, control_state = causal.load(control_path), causal.load(control_path.with_suffix(".state.json"))
    full = old["entries"][queue.ORDER[1]]
    validate_original(
        old, old_state, control, control_state,
        full_registered=any(Path(full[name]).exists() for name in (
            "position_registration", "comparison_registration",
        )),
        old_watcher_alive=causal.reservation.alive(old_state.get("watcher")),
    )
    if control_state["status"] != "collected_performance_review_pending" and not causal.reservation.alive(
        control_state.get("watcher")
    ):
        raise ValueError("registered control is no longer supervised")
    frozen = Path(old["commands"][queue.ORDER[1]][1]).resolve().parents[1]
    if git_source_state(frozen) != old["application"]:
        raise ValueError("the original frozen qualification or pending Full sources changed")
    for path, digest in old["inputs_sha256"].items():
        if sha256_file(Path(path)) != digest:
            raise ValueError("original registered input changed: " + path)
    causal.comparison.verify_sources(control)
    evidence = causal.load(args.operator_evidence)
    if (
        evidence["returncode"] != 0 or not evidence["sources_unchanged_during_execution"]
        or sha256_file(Path(evidence["report"]["path"])) != evidence["report"]["sha256"]
        or any(sha256_file(ROOT / name) != digest for name, digest in evidence["source_sha256"].items())
    ):
        raise ValueError("replacement CPU monitor lacks current source-bound tests")
    args.output_dir.mkdir(parents=True)
    inputs = {
        **old["inputs_sha256"],
        str(args.original_plan.resolve()): sha256_file(args.original_plan),
        str(old_state_path.resolve()): sha256_file(old_state_path),
        str(control_path): sha256_file(control_path),
        str(args.operator_evidence.resolve()): sha256_file(args.operator_evidence),
    }
    plan = {
        **old, "application": application, "authorization": authorization,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "inputs_sha256": inputs, "adopt_existing_comparisons": [queue.ORDER[0]],
        "source_formal_application": old["application"],
        "replacement_scope": "CPU_monitor_only;existing_control_and_pending_full_command_unchanged",
    }
    path = args.output_dir / "registration.json"
    causal.reservation.write_json(path, plan)
    queue.verify(plan)
    with (args.output_dir / "watcher.log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(ROOT / "experiments/reports/prefill2048_mean120_20261029/queue_pair.py"),
             "--watch", str(path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            start_new_session=True,
        )
    print({"registration": str(path), "watcher": causal.reservation.process(watcher.pid)})


if __name__ == "__main__":
    main()
