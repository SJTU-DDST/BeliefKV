"""CPU-only complete-run plots with explicit engine and profiling differences."""

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
FULL = OPS / "control_plan_diagnostics_20261028_r1.registration.json"
CONTROL = OPS / "v345_locobench_categorized_thunder_decay_20261027.registration.json"
STATE = OPS / "control_plan_diagnostics_20261028_r1.plots_r2.state.json"
OUTPUT = ROOT / "experiments/figures/control_plan_diagnostics_20261028_r1"


def main():
    sys.path.insert(0, json.loads(FULL.read_bytes())["execution_checkout"])
    from beliefkv.experiments.native_engine_variant import COMMON_TREE, SIBLING_CAPACITY_TREE
    from beliefkv.experiments.source_provenance import sha256_file
    from scripts import run_causal_comparison as causal

    def released(plan, path):
        return causal.predecessor_ready({**plan, "after_registration": str(path)})

    def validate():
        full, control = causal.load(FULL), causal.load(CONTROL)
        if (
            full["order"] != ["full"] or control["order"] != ["thunderagent_acting_decay"]
            or full["engine_tree"] != SIBLING_CAPACITY_TREE
            or control["engine_tree"] != COMMON_TREE or full["quality_exclusions"]
            or full["reference_environment"].get(
                "BELIEFKV_PROFILE_SHARED_PATH",
                full["reference_environment"].get("PROFILE_SHARED_PATH", "0"),
            ) != "1"
            or control["reference_environment"].get(
                "BELIEFKV_PROFILE_SHARED_PATH",
                control["reference_environment"].get("PROFILE_SHARED_PATH", "0"),
            ) != "0"
        ):
            raise ValueError("unexpected arms, engine pair, profiling or exclusions")
        for field in (
            "source_plan", "token_source_sha256", "source_capacity", "source_server_parameters",
            "workflow_ids", "quality_exclusions", "quality_exclusion_details",
            "sampling_mode", "stream_response_metrics", "request_timeout_s", "max_running_requests",
        ):
            if full.get(field) != control.get(field):
                raise ValueError("common comparison field differs: " + field)
        overlays = []
        for plan in (full, control):
            causal.comparison.verify_sources(plan)
            overlay = plan["tool_timing_overlay"]
            if sha256_file(Path(overlay["path"])) != overlay["sha256"]:
                raise ValueError("tool timing changed")
            overlays.append({key: value for key, value in overlay.items() if key != "path"})
        if overlays[0] != overlays[1]:
            raise ValueError("tool timing provenance differs")
        return full, control

    rendering = "--render" in sys.argv
    if OUTPUT.exists() or (not rendering and STATE.exists()):
        raise FileExistsError("preserve previous plotting state and figures")
    pins = {str(path): sha256_file(path) for path in (FULL, CONTROL, Path(__file__))}

    def stamp(status, **extra):
        causal.reservation.write_json(STATE, {
            "status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
            "pins": pins, "gpu_work_authorized": False, "performance_accepted": False, **extra,
        })

    def check_pins():
        if any(sha256_file(Path(path)) != digest for path, digest in pins.items()):
            raise ValueError("pinned plotting input changed")

    try:
        full, control = validate()
        if not released(control, CONTROL):
            raise ValueError("reused complete ThunderAgent has not exited")
        if not rendering:
            started = time.monotonic()
            stamp("waiting_complete_full", process=causal.reservation.process(os.getpid()))
            while not released(full, FULL):
                check_pins()
                if time.monotonic() - started > 4 * 3600:
                    raise TimeoutError("Full exceeded the plotting wait window")
                time.sleep(10.)
            check_pins()
            subprocess.run(
                [str(OPS / "cpu_plotting_20261027/bin/python"), str(Path(__file__)), "--render"],
                check=True, env={**os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21"},
            )
            stamp("complete", output=str(OUTPUT))
            return
        if not released(full, FULL):
            raise ValueError("render requires complete Full and actual owned exit")
        from scripts import plot_causal_decode_diagnostics as plotting

        labels = ("Full (profiled)", "ThunderAgent decay")
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
            title="V311 work: profiled Full (sibling engine) / ThunderAgent decay (common engine)",
        )
        for name, field in (("time_bins.csv", "bins"), ("geometry.csv", "geometry")):
            plotting.write_csv(OUTPUT / name, [
                {"arm": label, **row} for label, arm in arms.items() for row in arm[field]
            ])
        result = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "scope": "complete_same_work_with_explicit_engine_and_profiling_differences",
            "native_engines": {labels[0]: SIBLING_CAPACITY_TREE, labels[1]: COMMON_TREE},
            "profiling_enabled": {labels[0]: True, labels[1]: False},
            "bin_seconds": 60, "arms": arms, "figures": figures,
            "registration_sha256": {str(path): sha256_file(path) for path in (FULL, CONTROL)},
            "operator_sha256": sha256_file(Path(__file__)),
            "plotting_operator_sha256": sha256_file(Path(plotting.__file__)),
            "performance_accepted": False,
            "limitations": [
                "Engine and profiling differ; this diagnostic run is not an isolated module ablation.",
                "Retain all normal tails, actual partial bins and no post-completion zero padding.",
                "Worker intervals contain runtime and transfer dependencies, not pure GPU kernels.",
            ],
        }
        with (OUTPUT / "evidence.json.gz").open("xb") as stream:
            stream.write(gzip.compress(json.dumps(result, indent=2, allow_nan=False).encode(), mtime=0))
    except BaseException as error:
        stamp("stopped_without_gpu_action", error=repr(error))
        raise


if __name__ == "__main__":
    main()
