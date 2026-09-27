#!/usr/bin/env python3
"""Score frozen unseen-project JOIN evidence without fitting on test outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.evaluate_cold_tool_project_loo import require_complete_batch
from scripts.evaluate_join_service_window_gate import evaluate


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_sealed_run(
    frozen_test: Path, provenance: Path, heldout_workflows: Path,
) -> dict:
    test, source = _load(frozen_test), _load(provenance)
    expected = source["test_ids"]
    ids = [row["instance_id"] for row in test["workloads"]]
    if (
        test.get("split") != "test_id"
        or ids != expected
        or len(ids) != len(set(ids))
        or len(ids) != source["test_workflows"]
        or sorted({id_.split("__", 1)[0] for id_ in ids})
        != sorted(source["test_projects"])
    ):
        raise ValueError("sealed test manifest differs from frozen provenance")
    run = _load(heldout_workflows.parent / "manifest.json")
    complete_ids, errors = require_complete_batch(heldout_workflows)
    if (
        run.get("workload_manifest_sha256") != _sha256(frozen_test)
        or run.get("instance_ids") != ids
        or complete_ids != ids
    ):
        raise ValueError("run does not cover the frozen test manifest")
    config = run["config"]
    if (
        config["loop_guard"]["activation_wall_clock_s"] != 14400
        or config["request_timeout_s"] != 7200
        or config["model"] != "Qwen3.5-35B-A3B"
        or config["subagent_fanout_profile"] != "native_dynamic_1to4"
    ):
        raise ValueError("sealed run used an unexpected agent configuration")
    return {
        "test_manifest_sha256": _sha256(frozen_test),
        "provenance_sha256": _sha256(provenance),
        "test_projects": source["test_projects"],
        "test_workflows": len(ids),
        "runner_errors": errors,
    }


def score_sealed(
    frozen_test: Path,
    provenance: Path,
    train: list[tuple[Path, Path]],
    heldout: tuple[Path, Path],
) -> dict:
    identity = validate_sealed_run(frozen_test, provenance, heldout[0])
    result = evaluate(train, heldout)
    if sorted(result["heldout_source"]["frozen_projects"]) != sorted(
        identity["test_projects"]
    ):
        raise ValueError("scored projects differ from the frozen test projects")
    result.update({
        "status": "project_disjoint_sealed_shadow_not_action_eligible",
        "sealed_identity": identity,
        "scope": (
            "Frozen unseen-project shadow evaluation: training-only JOIN ETA "
            "and pressure gate are applied without fitting to sealed outcomes. "
            "Coverage and natural-return selection must be reported alongside "
            "point error. First service is posthoc; no predictive H2D, "
            "capacity reservation, or throughput benefit was tested."
        ),
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-test", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument(
        "--train", nargs=2, action="append", type=Path, metavar=("WORKFLOWS", "AUDIT"),
        required=True,
    )
    parser.add_argument("--heldout", nargs=2, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = score_sealed(
        args.frozen_test, args.provenance,
        [(workflows, audit) for workflows, audit in args.train],
        tuple(args.heldout),
    )
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
