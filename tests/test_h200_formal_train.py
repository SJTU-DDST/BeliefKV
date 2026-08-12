from __future__ import annotations

import pytest

from scripts.prepare_h200_formal_train import _select_quota


def _rows(project: str, count: int) -> list[dict[str, str]]:
    return [
        {
            "repo": project,
            "instance_id": f"{project.replace('/', '__')}-{index}",
            "version": f"v{index % 4}",
        }
        for index in range(count)
    ]


def test_formal_train_selection_is_deterministic_and_cross_shard_disjoint() -> None:
    quotas = {"org/a": 4, "org/b": 3, "org/c": 1}
    rows = [row for project in quotas for row in _rows(project, 16)]
    development = {"org__a-0", "org__b-0"}

    first = _select_quota(
        rows,
        quotas=quotas,
        excluded=development,
        seed="fixture",
        shard_id="shard-1",
    )
    repeated = _select_quota(
        rows,
        quotas=quotas,
        excluded=development,
        seed="fixture",
        shard_id="shard-1",
    )
    first_ids = {row["instance_id"] for row in first}
    second = _select_quota(
        rows,
        quotas=quotas,
        excluded=development | first_ids,
        seed="fixture",
        shard_id="shard-2",
    )
    second_ids = {row["instance_id"] for row in second}

    assert first == repeated
    assert len(first_ids) == len(second_ids) == sum(quotas.values())
    assert first_ids.isdisjoint(development)
    assert second_ids.isdisjoint(development | first_ids)
    for project, quota in quotas.items():
        assert sum(row["repo"] == project for row in first) == quota
        assert sum(row["repo"] == project for row in second) == quota


def test_formal_train_selection_rejects_unavailable_quota() -> None:
    with pytest.raises(ValueError, match="has 1 candidates; 2 required"):
        _select_quota(
            _rows("org/a", 1),
            quotas={"org/a": 2},
            excluded=set(),
            seed="fixture",
            shard_id="shard-1",
        )
