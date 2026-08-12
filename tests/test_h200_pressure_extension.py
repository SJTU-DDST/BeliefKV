from __future__ import annotations

import pytest

from scripts.prepare_h200_pressure_extension import _parse_quotas, _select


def _rows(project: str, count: int) -> list[dict[str, str]]:
    return [
        {
            "repo": project,
            "instance_id": f"{project.replace('/', '__')}-{index}",
            "version": f"v{index % 3}",
        }
        for index in range(count)
    ]


def test_pressure_extension_is_deterministic_quota_aware_and_disjoint() -> None:
    quotas = {"org/a": 4, "org/b": 2, "org/c": 1}
    rows = [row for project in quotas for row in _rows(project, 8)]
    selected = _select(
        rows,
        quotas=quotas,
        excluded={"org__a-0", "org__b-0"},
        seed="fixture",
    )
    repeated = _select(
        rows,
        quotas=quotas,
        excluded={"org__a-0", "org__b-0"},
        seed="fixture",
    )

    assert selected == repeated
    assert len(selected) == 7
    assert len({row["instance_id"] for row in selected}) == 7
    assert {row["instance_id"] for row in selected}.isdisjoint(
        {"org__a-0", "org__b-0"}
    )
    for project, quota in quotas.items():
        assert sum(row["repo"] == project for row in selected) == quota


def test_pressure_extension_rejects_invalid_or_unavailable_quota() -> None:
    with pytest.raises(ValueError, match="invalid or duplicate quota"):
        _parse_quotas(["org/a=0"])
    with pytest.raises(ValueError, match="unused train tasks"):
        _select(
            _rows("org/a", 1),
            quotas={"org/a": 2},
            excluded=set(),
            seed="fixture",
        )
