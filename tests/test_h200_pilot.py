from __future__ import annotations

from scripts.prepare_h200_pilot import FANOUT_PROFILES, _select_rows


def _rows(project: str, count: int) -> list[dict[str, str]]:
    return [
        {
            "repo": project,
            "instance_id": f"{project.replace('/', '__')}-{index}",
            "version": f"v{index % 3}",
        }
        for index in range(count)
    ]


def test_pilot_selection_is_deterministic_disjoint_and_balanced() -> None:
    projects = ("org/a", "org/b", "org/c", "org/d")
    rows = [row for project in projects for row in _rows(project, 8)]
    selected = _select_rows(
        rows,
        projects=projects,
        excluded={"org__a-0"},
        seed="fixture",
        tasks_per_project_per_arm=2,
    )
    repeated = _select_rows(
        rows,
        projects=projects,
        excluded={"org__a-0"},
        seed="fixture",
        tasks_per_project_per_arm=2,
    )

    assert selected == repeated
    all_ids = [
        row["instance_id"]
        for profile in FANOUT_PROFILES
        for row in selected[profile]
    ]
    assert len(all_ids) == len(set(all_ids)) == 16
    assert "org__a-0" not in all_ids
    for profile in FANOUT_PROFILES:
        assert len(selected[profile]) == 8
        counts = {
            project: sum(row["repo"] == project for row in selected[profile])
            for project in projects
        }
        assert set(counts.values()) == {2}
