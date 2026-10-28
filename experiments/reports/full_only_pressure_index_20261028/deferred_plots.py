"""CPU-only complete-run plots with the reviewed native-engine difference."""

from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
OPS = ROOT.parent / "ops"
FULL = OPS / "full_only_pressure_index_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
STATE = OPS / "full_only_pressure_index_20261028_r1.plots.state.json"
OUTPUT = ROOT / "experiments/figures/full_only_pressure_index_20261028_r1"


def main():
    sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])
    from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal

    rendering = "--render" in sys.argv
    if OUTPUT.exists() or not rendering and STATE.exists():
        raise FileExistsError("preserve prior plotting state and artifacts")
    pins = {str(path): sha256_file(path) for path in (FULL, CONTROL, Path(__file__))}

    def stamp(status, **extra):
        causal.reservation.write_json(STATE, {
            "status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
            "inputs_sha256": pins, "gpu_work_authorized": False,
            "performance_accepted": False, **extra,
        })

    def released(plan, path):
        return causal.predecessor_ready({**plan, "after_registration": str(path)})

    try:
        full, control = causal.load(FULL), causal.load(CONTROL)
        if (
            full["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]
            or full["engine_tree"] != SIBLING_CAPACITY_TREE or control["engine_tree"] != COMMON_TREE
            or full["quality_exclusions"]
            or any(plan["reference_environment"].get(
                "BELIEFKV_PROFILE_SHARED_PATH",
                plan["reference_environment"].get("PROFILE_SHARED_PATH", "0"),
            ) != "0" for plan in (full, control))
            or not released(control, CONTROL)
        ):
            raise ValueError("unexpected work, engine, profile or incomplete control")
        if not rendering:
            stamp("waiting_complete_full", process=causal.reservation.process(os.getpid()))
            deadline = time.monotonic() + 4 * 3600.
            while not released(full, FULL):
                if time.monotonic() > deadline:
                    raise TimeoutError("Full exceeded the bounded plotting wait")
                if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
                    raise ValueError("pinned input changed")
                time.sleep(10.)
            subprocess.run(
                [str(OPS / "cpu_plotting_20261027/bin/python"), str(Path(__file__)), "--render"],
                check=True, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            )
            stamp("complete", output=str(OUTPUT))
            return
        if not released(full, FULL):
            raise ValueError("render requires complete Full and actual owned exit")
        for plan in (full, control):
            causal.comparison.verify_sources(plan)
        from scripts import plot_causal_decode_diagnostics as plotting

        labels = ("Full (pressure index)", "ThunderAgent decay")
        arms = {
            labels[0]: plotting.extract(labels[0], FULL, Path(full["run_root"]) / "full", bin_s=60),
            labels[1]: plotting.extract(
                labels[1], CONTROL, Path(control["run_root"]) / "thunderagent_acting_decay", bin_s=60,
            ),
        }
        plotting.require_matching({
            label: {"match_contract": {
                key: value for key, value in arm["match_contract"].items() if key != "engine_tree"
            }} for label, arm in arms.items()
        })
        OUTPUT.mkdir(parents=True)
        figures = plotting.render(
            arms, OUTPUT, bin_s=60,
            title="Same V311 work: Full (sibling engine) / ThunderAgent decay (common engine)",
        )
        for name, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
            plotting.write_csv(OUTPUT / name, [
                {"arm": label, **row} for label, arm in arms.items() for row in arm[field]
            ])
        with (OUTPUT / "evidence.json.gz").open("xb") as stream:
            stream.write(gzip.compress(json.dumps({
                "observed_at": datetime.now(timezone.utc).isoformat(), "arms": arms,
                "figures": figures, "native_engines": {
                    labels[0]: SIBLING_CAPACITY_TREE, labels[1]: COMMON_TREE,
                },
                "profiling_enabled": {labels[0]: False, labels[1]: False},
                "registration_sha256": {str(path): sha256_file(path) for path in (FULL, CONTROL)},
                "operator_sha256": sha256_file(Path(__file__)),
                "all_normal_tails_retained": True, "performance_accepted": False,
                "limitations": [
                    "Reviewed native engines differ; this is a system comparison, not an isolated ablation.",
                    "Retain actual partial bins and no post-completion zero padding.",
                    "Worker intervals include runtime and dependencies, not pure CUDA kernels.",
                ],
            }, indent=2, allow_nan=False).encode(), mtime=0))
    except BaseException as error:
        stamp("stopped_without_gpu_action", error=repr(error))
        raise


if __name__ == "__main__":
    main()
