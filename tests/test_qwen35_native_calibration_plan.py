from pathlib import Path

import pytest

from beliefkv.experiments.p6_collection import load_collection_batch
from scripts.freeze_qwen35_native_calibration_plan import freeze_calibration_plan


ROOT = Path(__file__).resolve().parents[1]


def test_calibration_plan_contains_only_frozen_unseen_projects(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    workload_path = tmp_path / "workload.json"
    plan = freeze_calibration_plan(
        ROOT / "workloads/raw/swebench_verified-91aa3ed",
        ROOT / "configs/p6/swebench_verified_split_v1.json",
        ROOT / "workloads/sources/p6_swebench_verified",
        workload_path, plan_path,
    )
    batch = load_collection_batch(
        plan_path, plan["batches"][0]["batch_id"], allow_calibration=True,
    )
    assert batch.workflow_count == 66
    assert batch.split == "calibration"
    assert batch.workflow_arrival_batch_size == 33
    assert set(plan["batches"][0]["projects"]) == {
        "astropy/astropy", "sphinx-doc/sphinx",
    }
    assert plan["arrival_contract"]["server_max_running_requests"] == 48
    with pytest.raises((ValueError, PermissionError)):
        load_collection_batch(plan_path, batch.batch_id)
    with pytest.raises(FileExistsError):
        freeze_calibration_plan(
            ROOT / "workloads/raw/swebench_verified-91aa3ed",
            ROOT / "configs/p6/swebench_verified_split_v1.json",
            ROOT / "workloads/sources/p6_swebench_verified",
            workload_path, plan_path,
        )
