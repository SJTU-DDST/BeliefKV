from __future__ import annotations

import json
from pathlib import Path

import pytest

from beliefkv.experiments.swebench_images import swebench_instance_image


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_swebench_image_key_matches_frozen_collection() -> None:
    plan = json.loads(
        (
            REPOSITORY_ROOT / "configs/p6/collection_v4/collection_plan.json"
        ).read_text(encoding="utf-8")
    )
    checked = 0
    for batch in plan["batches"]:
        manifest = json.loads(
            Path(batch["workload_manifest"]).read_text(encoding="utf-8")
        )
        for workload in manifest["workloads"]:
            assert swebench_instance_image(workload["instance_id"]) == workload[
                "docker_image"
            ]
            checked += 1

    assert checked == plan["workflow_count"]


def test_swebench_image_key_rejects_malformed_instance() -> None:
    with pytest.raises(ValueError, match="invalid SWE-bench"):
        swebench_instance_image("not-an-instance")
