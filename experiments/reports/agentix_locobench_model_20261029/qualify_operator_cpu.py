"""Bind the new Agentix operator checks; unchanged physical server proofs stay separate."""

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from beliefkv.experiments.source_provenance import sha256_file
from experiments.reports.kvflow_radix_session_20261028.run_bound_cpu import source_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="operator_cpu")
    args = parser.parse_args()
    if Path(args.name).name != args.name:
        raise ValueError("CPU evidence name must be a basename")
    xml, output = HERE / f"{args.name}.xml", HERE / f"{args.name}.json"
    if xml.exists() or output.exists():
        raise FileExistsError("preserve previous operator checks")
    before = source_manifest()
    command = [
        sys.executable, "-m", "pytest", "-q", "tests/test_agentix_locobench_model.py",
        "--tb=short", "--junitxml=" + str(xml),
    ]
    result = subprocess.run(command, cwd=ROOT, check=False)
    if before != source_manifest():
        raise ValueError("operator sources changed during CPU checks")
    suites = ET.parse(xml).findall(".//testsuite")
    totals = {
        name: sum(int(row.attrib[name]) for row in suites)
        for name in ("tests", "errors", "failures", "skipped")
    }
    evidence = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "New numerical-probe operator, real runtime event ingress, CPU HTTP/model stand-ins.",
        "command": command, "returncode": result.returncode, "totals": totals,
        "sources_unchanged_during_execution": True, "source_manifest": before,
        "report": {"path": str(xml), "sha256": sha256_file(xml)},
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "nice": os.getpriority(os.PRIO_PROCESS, os.getpid()),
        "formal_baseline_qualified": False, "gpu_experiment_started": False,
    }
    with output.open("x") as stream:
        json.dump(evidence, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if result.returncode or any(totals[key] for key in ("errors", "failures", "skipped")):
        raise ValueError("Agentix operator CPU checks failed")


if __name__ == "__main__":
    main()
