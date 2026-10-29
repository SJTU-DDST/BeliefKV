from copy import deepcopy

from experiments.reports.workflow_work_training_20261029.calibrate_current import prepare, select
from tests.test_workflow_work_capture import row


def test_current_profile_split_keeps_all_snapshots_together_without_label_selection(tmp_path):
    source = str((tmp_path / "new").resolve())
    current = [
        {**row(source, hex(i)[2:], snapshot=j), "workflow": source + ":" + str(i)}
        for i in range(8) for j in range(2)
    ]
    train = {"rows": [row("old", "a"), *current], "sources_sha256": {}}
    cal = {"rows": [row("cal", "b")], "sources_sha256": {}}
    evaluation = {"rows": [row("test", "c")]}
    training, calibration, split = prepare(train, cal, evaluation, tmp_path / "new")
    assert len(split["current_training_workflows"]) == len(split["current_calibration_workflows"]) == 4
    assert {r["workflow"] for r in training["rows"]}.isdisjoint(
        {r["workflow"] for r in calibration["rows"]},
    )
    changed = deepcopy(train)
    for value in changed["rows"]:
        value["remaining_work"] = value["total_work"] = 1e9
    assert prepare(changed, cal, evaluation, tmp_path / "new")[2] == split


def test_model_selection_uses_only_calibration_quality():
    reports = {
        "first": {
            "decision_eligible": True, "calibration": {"macro_f1": .7, "long_to_short_rate": .03},
            "test": {"macro_f1": 0.},
        },
        "second": {
            "decision_eligible": True, "calibration": {"macro_f1": .6, "long_to_short_rate": .01},
            "test": {"macro_f1": 1.},
        },
    }
    assert select(reports) == "first"
    for report in reports.values():
        report["decision_eligible"] = False
    assert select(reports) is None
