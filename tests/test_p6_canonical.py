from __future__ import annotations

import hashlib
import json
from pathlib import Path

from beliefkv.experiments.p6_canonical import build_canonical_source_specs
from beliefkv.experiments.p6_decision_points import _invocation_label
from scripts.prepare_h200_formal_calibration import (
    partition_calibration_rows,
    select_version_diverse_rows,
)


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_selection_replaces_instances_without_duplication(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "collection_plan.json"
    plan_sha = _write_json(
        plan_path,
        {
            "batches": [
                {"batch_id": "batch-a", "instance_ids": ["a", "b"]},
                {"batch_id": "batch-b", "instance_ids": ["c", "d"]},
            ]
        },
    )
    source_runs = []
    for batch_id in ("batch-a", "batch-b"):
        run = tmp_path / batch_id
        summary_sha = _write_json(run / "workloads" / "summary.json", {})
        contract_sha = _write_json(
            run / "server" / "runtime_profile_contract.json", {}
        )
        source_runs.append(
            {
                "batch_id": batch_id,
                "run_path": str(run),
                "summary_sha256": summary_sha,
                "runtime_contract_sha256": contract_sha,
            }
        )
    replacement_run = tmp_path / "replacement"
    replacement_contract_sha = _write_json(
        replacement_run / "server" / "runtime_profile_contract.json", {}
    )
    result = replacement_run / "shard" / "workloads" / "workflows" / "b" / "result.json"
    result_sha = _write_json(result, {"instance_id": "b"})
    selection = {
        "source_plan": {"path": str(plan_path), "sha256": plan_sha},
        "selection_rule": {"expected_unique_instances": 4},
        "source_runs": source_runs,
        "replacement_run": {
            "run_path": str(replacement_run),
            "runtime_contract_sha256": replacement_contract_sha,
        },
        "replacements": [
            {
                "instance_id": "b",
                "original_batch_id": "batch-a",
                "selected_result": str(result),
                "selected_result_sha256": result_sha,
            }
        ],
    }

    specs = build_canonical_source_specs(selection, repository_root=tmp_path)

    assert [spec.instance_ids for spec in specs] == [("a",), ("c", "d"), ("b",)]
    assert len({spec.run_dir for spec in specs}) == 3
    assert specs[-1].workload_dirs == (result.parents[2],)


def test_invocation_targets_are_state_specific() -> None:
    common = {
        "invocation_id": "inv",
        "ts_ms": 10.0,
        "request_id": "request",
        "boundaries": {
            "inv": [
                {"timestamp_ms": 20.0, "kind": "tool_end", "status": "success"}
            ]
        },
        "calls_by_invocation": {
            "inv": [
                {
                    "submit_ts_ms": 1.0,
                    "result_ts_ms": 9.0,
                    "context_tokens": 80,
                    "output_tokens": 20,
                },
                {
                    "submit_ts_ms": 30.0,
                    "result_ts_ms": 40.0,
                    "prompt_tokens": 120,
                    "output_tokens": 10,
                }
            ]
        },
        "calls_by_request": {
            "request": {
                "context_tokens": 100,
                "result_ts_ms": 20.0,
                "censored": False,
            }
        },
        "service_by_request": {
            "request": [
                {
                    "batch_service_complete_ts_ms": 15.0,
                    "phase": "decode",
                    "token_delta": 4,
                }
            ]
        },
    }

    running = _invocation_label(state="running_llm", **common)
    waiting_tool = _invocation_label(state="wait_tool", **common)
    waiting_join = _invocation_label(state="wait_join", **common)

    assert running["target_training_eligible"] == {
        "action_boundary": True,
        "remaining_decode_demand": True,
        "prompt_growth": False,
        "next_output_demand": False,
        "external_wait": False,
        "join_wait": False,
    }
    assert waiting_tool["target_training_eligible"]["external_wait"] is True
    assert waiting_tool["target_training_eligible"]["join_wait"] is False
    assert waiting_tool["target_training_eligible"]["prompt_growth"] is True
    assert waiting_tool["reentry_prompt_delta_tokens"] == 20
    assert waiting_join["target_training_eligible"]["external_wait"] is False
    assert waiting_join["target_training_eligible"]["join_wait"] is True


def test_calibration_selection_is_fixed_diverse_and_balanced() -> None:
    rows = [
        {
            "repo": project,
            "version": str(index % 5),
            "instance_id": f"{project}-{index}",
        }
        for project in ("astropy/astropy", "sphinx-doc/sphinx")
        for index in range(12)
    ]
    selected = {
        project: select_version_diverse_rows(
            rows, count=8, seed="fixed", project=project
        )
        for project in ("astropy/astropy", "sphinx-doc/sphinx")
    }
    assert all(
        len({row["version"] for row in values}) == 5
        for values in selected.values()
    )
    assert selected == {
        project: select_version_diverse_rows(
            rows, count=8, seed="fixed", project=project
        )
        for project in ("astropy/astropy", "sphinx-doc/sphinx")
    }

    shards = partition_calibration_rows(selected, seed="fixed")

    assert [len(shard) for shard in shards] == [8, 8]
    assert len({row["instance_id"] for shard in shards for row in shard}) == 16
    assert all(
        sum(row["repo"] == project for row in shard) == 4
        for shard in shards
        for project in ("astropy/astropy", "sphinx-doc/sphinx")
    )
