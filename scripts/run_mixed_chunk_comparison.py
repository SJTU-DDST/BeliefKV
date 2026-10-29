"""Qualify and run the matched mixed-chunk ThunderAgent/Full pair sequentially."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.native_engine_variant import engine_patch
from beliefkv.experiments.source_provenance import git_source_state, sha256_file
from experiments.reports.arrival_mean120_20261029.queue_pair import (
    complete_work, validate_cpu, validate_references,
)
from experiments.reports.prefill2048_mean120_20261029.queue_pair import (
    validate_native_rebinding, verify, watch,
)
from scripts import run_causal_comparison as causal
from scripts.prepare_mixed_chunk_comparison import ORDER, validate_profiles
from scripts.qualify_free_page_h2d_fence import check_reservation

MAIN = Path("/home/longhao/experiment/BeliefKV")
OPS = MAIN.parent / "ops"
PREVIOUS = {
    ORDER[0]: OPS / "prefill2048_mean120_thunder_20261029_r1.registration.json",
    ORDER[1]: OPS / "decay_tick2s_no_handoff_20261030_r1.registration.json",
}
LAST = OPS / "workflow_dispatch_20261029_r1.registration.json"
LAST_REVIEW = MAIN / (
    "experiments/reports/workflow_work_training_20261029/workflow_dispatch_r1/"
    "review_worker_r1/complete_review/review.json"
)


def register(args):
    if os.getpriority(os.PRIO_PROCESS, os.getpid()) != 0:
        raise ValueError("GPU launch queues must inherit nice0")
    application = git_source_state(ROOT)
    if application["dirty"] or args.output_dir.exists():
        raise ValueError("require clean committed sources and a fresh mixed queue")
    authorization = check_reservation()
    cpu = validate_cpu(args.cpu_evidence)
    profiles = ROOT / "configs/experiments/mixed_chunk_20261029"
    specifications = {label: causal.load(profiles / (label + ".json")) for label in ORDER}
    bases = validate_profiles(specifications)
    previous = {label: causal.load(path) for label, path in PREVIOUS.items()}
    validate_references(previous)
    work = {label: complete_work(path) for label, path in PREVIOUS.items()}
    latest_work = complete_work(LAST)
    if latest_work is None or any(item is None or item != latest_work for item in work.values()):
        raise ValueError("mixed comparison must follow complete exact work and actual owned exit")
    if causal.load(LAST_REVIEW).get("status") != "complete_matched_arrival_pair_reviewed":
        raise ValueError("review the completed dispatch candidate before another GPU comparison")
    args.output_dir.mkdir(parents=True)
    inputs = [
        args.cpu_evidence, Path(cpu["report"]["path"]), Path(cpu["source_manifest"]["path"]),
        LAST, LAST_REVIEW, *PREVIOUS.values(), *bases.values(),
        Path(specifications["full"]["workflow_arrivals"]["path"]),
    ]
    entries, commands = {}, {}
    for ordinal, label in enumerate(ORDER):
        old = previous[label]
        engine = Path(old["engine_checkout"])
        native = git_source_state(engine, canonical_patch=engine_patch(ROOT, old["engine_tree"]))
        qualified = causal.load(Path(old["engine_evidence"]["sources"]))
        validate_native_rebinding(native, qualified)
        native_path = args.output_dir / (label + ".native_sources.json")
        causal.reservation.write_json(native_path, native)
        evidence = causal.engine_evidence(engine, Path(old["engine_evidence"]["report"]), native_path)
        short = "thunder" if ordinal == 0 else "full"
        position = OPS / f"mixed_chunk_position_20261029_r1_{short}.registration.json"
        position_run = MAIN / f"experiments/raw/mixed_chunk_position_20261029_r1_{short}"
        formal = OPS / f"mixed_chunk_{short}_20261029_r1.registration.json"
        formal_run = MAIN / f"experiments/raw/mixed_chunk_20261029_r1_{short}"
        port = args.port + ordinal
        if any(path.exists() for path in (
            position, position.with_suffix(".state.json"), position.with_suffix(".log"),
            formal, formal.with_suffix(".state.json"), formal.with_suffix(".log"),
            position_run, formal_run, Path(f"/tmp/bkv-causal-{port}.sock"),
        )):
            raise FileExistsError("preserve previous mixed attempts; use fresh paths and sockets")
        specification = profiles / (label + ".json")
        commands[label] = [
            sys.executable, str(ROOT / "scripts/run_position_replay_calibration.py"),
            "--source-registration", str(OPS / "v230_locobench_parallel_retry_source_20261026.registration.json"),
            "--reference", str(OPS / "v128_eos_seeded_modules_resume_20261022.registration.json"),
            "--cpu-report", cpu["report"]["path"], "--cpu-sources", cpu["source_manifest"]["path"],
            "--engine", str(engine), "--engine-report", evidence["report"],
            "--engine-sources", str(native_path), "--registration", str(position),
            "--run-root", str(position_run), "--comparison-registration", str(formal),
            "--comparison-root", str(formal_run), "--port", str(port),
            "--experiment-config", str(specification),
        ]
        entries[label] = {
            "position_registration": str(position), "position_run": str(position_run),
            "comparison_registration": str(formal), "comparison_run": str(formal_run),
            "port": port,
        }
        inputs.extend((
            specification, native_path, Path(evidence["report"]),
            Path(old["engine_evidence"]["sources"]),
        ))
    plan = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "application": application, "authorization": authorization, "order": list(ORDER),
        "entries": entries, "commands": commands,
        "inputs_sha256": {str(path.resolve()): sha256_file(path) for path in inputs},
        "native_capture_repeated": False, "automatic_retry_after_failure": False,
        "actual_mixed_output_proof_required_before_formal": True,
        "shared_serving_tuning_not_agent_policy_innovation": True,
        "performance_accepted": False,
    }
    path = args.output_dir / "registration.json"
    causal.reservation.write_json(path, plan)
    verify(plan)
    with (args.output_dir / "watcher.log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--watch", str(path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            start_new_session=True,
        )
    print(json.dumps({"registration": str(path), "watcher": causal.reservation.process(watcher.pid)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-evidence", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--port", type=int, default=18895)
    parser.add_argument("--watch", type=Path)
    args = parser.parse_args()
    if args.watch:
        watch(args.watch)
    elif args.cpu_evidence and args.output_dir and 1 <= args.port <= 65534:
        register(args)
    else:
        parser.error("require source-bound CPU evidence and a fresh queue directory, or --watch")


if __name__ == "__main__":
    main()
