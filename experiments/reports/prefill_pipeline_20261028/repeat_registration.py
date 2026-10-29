"""Repeat the exact qualified R1 source through its original frozen supervisor."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OPS = ROOT.parent / "ops"
PRIOR = OPS / "demand_prefill_pipeline_20261028_r1.registration.json"
TARGET = OPS / "demand_prefill_pipeline_20261028_r2.registration.json"
RUN_ROOT = ROOT / "experiments/raw/demand_prefill_pipeline_20261028_r2"
CHECK = HERE / "repeat_r2_entry_check.json"
PORT = 18696
METADATA_FIELDS = frozenset((
    "registered_at", "run_root", "port", "after_registration", "external_inputs", "repeat_of",
))


def validate_metadata(original, repeated):
    changed = {
        key for key in original.keys() | repeated.keys()
        if original.get(key) != repeated.get(key)
    }
    if not changed <= METADATA_FIELDS:
        raise ValueError("repeat changes serving source, configuration or qualified work")
    if repeated["external_inputs"][:len(original["external_inputs"])] != original["external_inputs"]:
        raise ValueError("repeat removed or changed original qualified inputs")
    if repeated["run_root"] == original["run_root"] or repeated["port"] == original["port"]:
        raise ValueError("repeat must have fresh output and transport identity")
    return sorted(changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args()
    original = json.loads(PRIOR.read_bytes())
    sys.path.insert(0, original["execution_checkout"])
    from scripts import run_causal_comparison as causal

    if (
        Path(causal.__file__).resolve()
        != Path(original["execution_checkout"]) / "scripts/run_causal_comparison.py"
        or causal.sha256_file(Path(causal.__file__))
        != original["source_sha256"]["scripts/run_causal_comparison.py"]
        or original["order"] != ["full"]
        or original["code_commit"] != "01510ed528c2247f3ce0b370151381b04fefea9e"
        or original["engine_tree"] != "729aa0caa7a1ffac84463f46b3d1843d1a6a22e4"
    ):
        raise ValueError("repeat requires the exact qualified R1 execution entry")
    causal.comparison.verify_sources(original)
    causal.fresh.check_hold(original, released=True)
    if not causal.predecessor_ready({**original, "after_registration": str(PRIOR)}):
        raise ValueError("the original Full or an owned process remains live")
    complete_path, index_path = HERE / "complete_review.json", HERE / "final_index.json"
    complete, index = causal.load(complete_path), causal.load(index_path)
    if (
        not complete["two_fresh_idle_snapshots_and_actual_owned_exit_verified"]
        or not complete["exact_work_and_original_stopping_verified"]
        or not index["same_exact_work_and_original_stopping_verified"]
        or complete["proofs"]["Full (demand pipeline)"]["code_commit"] != original["code_commit"]
    ):
        raise ValueError("repeat requires the original complete exact work and cleanup")
    for source, digest in index["inputs_sha256"].items():
        if causal.sha256_file(Path(source)) != digest:
            raise ValueError("completed source or qualified evidence changed: " + source)
    arm = Path(original["run_root"]) / "full"
    work = causal.validate_result(
        original, causal.load(arm / "replay/summary.json"), arm, live_control=True,
    )
    causal.observed_configuration(original, "full", arm, final=True)
    if (len(work), sum(row[0] for row in work.values()), sum(row[1] for row in work.values())) != (
        3674, 145565966, 549671,
    ):
        raise ValueError("repeat work changed")
    files = (PRIOR, complete_path, index_path, Path(__file__))
    pins = {str(path): causal.sha256_file(path) for path in files}
    if CHECK.exists():
        checked = causal.load(CHECK)
        if checked["inputs_sha256"] != pins or checked["qualified_source_identical"] is not True:
            raise ValueError("repeat entry check changed")
    else:
        if args.register:
            raise ValueError("run the read-only repeat check before registration")
        causal.reservation.write_json(CHECK, {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Exact R1 reuse after complete work, cleanup, source and configuration checks.",
            "qualified_source_identical": True,
            "code_commit": original["code_commit"], "engine_tree": original["engine_tree"],
            "old_cpu_and_gpu_evidence_reused_by_exact_source_identity": True,
            "ordinary_math_long_greedy_equivalence_verified": False,
            "main_admission_retry_fix_included": False,
            "inputs_sha256": pins, "gpu_work_started": False,
            "performance_accepted": False,
        })
    if not args.register:
        print(json.dumps({"qualified_source_identical": True, "check": str(CHECK)}, indent=2))
        return
    for path in (
        TARGET, TARGET.with_suffix(".state.json"), TARGET.with_suffix(".log"), RUN_ROOT,
    ):
        if path.exists():
            raise FileExistsError("preserve existing repeat registration and output")
    repeated = deepcopy(original)
    repeated.update({
        "registered_at": causal.reservation.now().isoformat(),
        "run_root": str(RUN_ROOT), "port": PORT,
        "after_registration": str(PRIOR),
        "repeat_of": {
            "registration": str(PRIOR), "registration_sha256": pins[str(PRIOR)],
            "entry_check": str(CHECK), "entry_check_sha256": causal.sha256_file(CHECK),
            "serving_code_configuration_work_and_stopping_identical": True,
            "main_admission_retry_fix_included": False,
        },
    })
    repeated["external_inputs"].extend({
        "path": str(path), "sha256": causal.sha256_file(path),
    } for path in (*files, CHECK) if not any(
        item["path"] == str(path) for item in repeated["external_inputs"]
    ))
    changed = validate_metadata(original, repeated)
    causal.comparison.verify_sources(repeated)
    if not causal.predecessor_ready(repeated):
        raise ValueError("the source predecessor has not released the GPU queue")
    causal.reservation.write_json(TARGET, repeated)
    with TARGET.with_suffix(".log").open("x") as output:
        watcher = subprocess.Popen(
            [sys.executable, str(Path(original["execution_checkout"]) / "scripts/run_causal_comparison.py"),
             "--watch", str(TARGET)],
            cwd=original["execution_checkout"],
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(json.dumps({
        "registration": str(TARGET), "watcher": causal.reservation.process(watcher.pid),
        "code_commit": repeated["code_commit"], "engine_tree": repeated["engine_tree"],
        "changed_metadata_only": changed,
        "main_admission_retry_fix_included": False, "performance_accepted": False,
    }, indent=2))


if __name__ == "__main__":
    main()
