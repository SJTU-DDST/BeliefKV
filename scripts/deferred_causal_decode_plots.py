#!/usr/bin/env python3
"""Draw matched decode diagnostics after complete success and actual owned exit."""

import argparse
from datetime import datetime, timezone
import gzip
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from scripts.run_causal_comparison import load, predecessor_ready, reservation


def released(path, label):
    plan = load(path)
    if plan["order"] != [label]:
        raise ValueError("plotting requires one complete declared arm")
    return predecessor_ready({**plan, "after_registration": str(path)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-registration", type=Path, required=True)
    parser.add_argument("--control-registration", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--plot-python", type=Path, required=True)
    args = parser.parse_args()
    if args.state.exists() or args.output_dir.exists():
        raise FileExistsError("preserve existing plotting evidence")
    pins = {
        str(path): sha256_file(path)
        for path in (args.full_registration, args.control_registration)
    }

    def stamp(status, **extra):
        reservation.write_json(args.state, {
            "status": status, "observed_at": datetime.now(timezone.utc).isoformat(),
            "registration_sha256": pins, "output": str(args.output_dir),
            "gpu_work_authorized": False, "performance_accepted": False, **extra,
        })

    try:
        if not released(args.control_registration, "thunderagent_acting_decay"):
            raise ValueError("matched control has not completed and exited")
        full, control = load(args.full_registration), load(args.control_registration)
        stamp("waiting_complete_full")
        started = time.monotonic()
        while not released(args.full_registration, "full"):
            if any(sha256_file(Path(path)) != value for path, value in pins.items()):
                raise ValueError("registered inputs changed")
            if time.monotonic() - started > 4 * 3600:
                raise TimeoutError("Full has not completed within four hours")
            time.sleep(10)
        if any(sha256_file(Path(path)) != value for path, value in pins.items()):
            raise ValueError("registered inputs changed at completion")
        checkout = Path(full["execution_checkout"])
        script = checkout / "scripts/plot_causal_decode_diagnostics.py"
        if sha256_file(script) != full["source_sha256"][str(script.relative_to(checkout))]:
            raise ValueError("frozen plotting operator changed")
        stamp("plotting_complete_matched_runs")
        subprocess.run([
            str(args.plot_python), str(script),
            "--arm", f"Full={full['run_root']}/full",
            "--registration", f"Full={args.full_registration}",
            "--arm", f"ThunderAgent decay={control['run_root']}/thunderagent_acting_decay",
            "--registration", f"ThunderAgent decay={args.control_registration}",
            "--bin-seconds", "60", "--title", "Matched V311 timing: reference-core Full",
            "--output-dir", str(args.output_dir),
        ], check=True, cwd=checkout, env={
            **os.environ, "CUDA_VISIBLE_DEVICES": "99999", "PYTHONHASHSEED": "21",
        })
        source = args.output_dir / "evidence.json"
        target = args.output_dir / "evidence.json.gz"
        with source.open("rb") as src, target.open("xb") as dst:
            with gzip.GzipFile(fileobj=dst, mode="wb", filename="", mtime=0) as stream:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    stream.write(chunk)
        if gzip.decompress(target.read_bytes()) != source.read_bytes():
            raise ValueError("evidence compression mismatch")
        source.unlink()
        stamp("complete")
    except BaseException as error:
        stamp("stopped_without_gpu_action", error=repr(error))
        raise


if __name__ == "__main__":
    main()
