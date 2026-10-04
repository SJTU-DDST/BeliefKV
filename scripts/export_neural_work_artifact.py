#!/usr/bin/env python3
"""Export a locally fitted work candidate without changing the phase head."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import torch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-artifact", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    phase = json.loads(args.phase_artifact.read_text())
    report = json.loads((args.candidate / "report.json").read_text())
    if report["phase_artifact_sha256"] != hashlib.sha256(args.phase_artifact.read_bytes()).hexdigest():
        raise ValueError("candidate was fitted against another phase artifact")
    fitted = torch.load(args.candidate / "conditional_work_candidate.pt", map_location="cpu", weights_only=False)
    state = fitted["state_dict"]
    work = {
        "schema_version": 1, "kind": "neural_conditional_work",
        "encoder_weights_sha256": phase["metadata"]["adapted_encoder"]["weights_sha256"],
        "center": fitted["center"].tolist(), "scale": fitted["scale"].tolist(),
        "layers": [{
            "weight": state[f"{i}.weight"].tolist(), "bias": state[f"{i}.bias"].tolist(),
        } for i in (0, 2, 4)],
        "target": fitted["selected"]["target"],
        "token_bias": fitted["selected"]["bias"],
        "interval_margin_tokens": fitted["interval_margin_tokens"],
        "scope": "conditional work only; no physical action authorization",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "neural_work.json"
    path.write_text(json.dumps(work, indent=2) + "\n")
    composite = copy.deepcopy(phase)
    composite["conditional_work_head"] = {
        "path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    (args.output / "semantic_event_calibrated.json").write_text(json.dumps(composite, indent=2) + "\n")
    out_report = copy.deepcopy(report)
    out_report["calibration"] = json.loads((args.phase_artifact.parent / "report.json").read_text())["calibration"]
    (args.output / "report.json").write_text(json.dumps(out_report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "phase_unchanged": True}))


if __name__ == "__main__":
    main()
