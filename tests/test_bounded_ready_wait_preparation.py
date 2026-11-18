from copy import deepcopy
import json
from pathlib import Path

import pytest

from beliefkv.runtime.ready_wait_priority import ReadyWaitSettings
from scripts.prepare_bounded_ready_wait import prepare
from scripts.prepare_maximal_cost_admission import prepare_artifacts as maximal
from scripts.prepare_shortest_work_admission import prepare_artifacts as shortest
from scripts.train_workflow_work import write
from beliefkv.experiments.source_provenance import sha256_file
from tests.test_model_work_choice_audit import model_work_pair


def prepared_pair(tmp_path):
    _full, oracle, learned = model_work_pair(tmp_path)
    paths = {
        name: Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
        for name, config in (("oracle", oracle), ("learned", learned))
    }
    model, truth = maximal(*shortest(*(
        json.loads(paths[name].read_bytes()) for name in ("learned", "oracle")
    )))
    model, truth = maximal(model, truth, selection_objective="benefit")
    for artifact in (model, truth):
        artifact["ready_wait_priority_ms"] = 30000
    paths = {
        "learned": tmp_path / "maximal-model-work.json",
        "oracle": tmp_path / "maximal-oracle-work.json",
    }
    write(paths["learned"], model)
    truth["combination_reference_source_model_sha256"] = sha256_file(paths["learned"])
    write(paths["oracle"], truth)
    result = {}
    for name, config in (("learned", learned), ("oracle", oracle)):
        for prefix in ("", "BELIEFKV_"):
            config["common_environment"][prefix + "WORKFLOW_WORK_MODEL"] = str(paths[name])
            config["common_environment"][prefix + "WORKFLOW_WORK_MODEL_SHA256"] = sha256_file(paths[name])
        result[name] = tmp_path / (name + "-config.json")
        write(result[name], config)
    return result


def test_actual_preparation_retains_predictor_traffic_and_matching_aging(tmp_path):
    paths = prepared_pair(tmp_path)
    before = {name: path.read_bytes() for name, path in paths.items()}
    output = tmp_path / "bounded"
    report = prepare(paths["learned"], paths["oracle"], output, ReadyWaitSettings(120000))
    assert report["GPU_action"] is False
    for name, path in paths.items():
        assert path.read_bytes() == before[name]
        old = json.loads(before[name])
        new = json.loads((output / (name + ".json")).read_bytes())
        for field in ("workflow_arrivals", "private_analysis_cohort", "tool_timing_overlay"):
            assert new.get(field) == old.get(field)
        work = json.loads((output / (name + "_work.json")).read_bytes())
        assert work["ready_wait_priority_ms"] == 120000
        assert work["ready_wait_priority_grace_ms"] == 60000
        assert work["ready_wait_priority_max_boost"] == 1.25
        left = deepcopy(old["common_environment"])
        right = deepcopy(new["common_environment"])
        for prefix in ("", "BELIEFKV_"):
            for field in ("WORKFLOW_WORK_MODEL", "WORKFLOW_WORK_MODEL_SHA256"):
                left.pop(prefix + field)
                right.pop(prefix + field)
        assert left == right
    with pytest.raises(FileExistsError):
        prepare(paths["learned"], paths["oracle"], output, ReadyWaitSettings(120000))


def test_preparation_refuses_changed_input_artifacts(tmp_path):
    paths = prepared_pair(tmp_path)
    config = json.loads(paths["oracle"].read_bytes())
    artifact = Path(config["common_environment"]["WORKFLOW_WORK_MODEL"])
    raw = json.loads(artifact.read_bytes())
    raw["ready_wait_priority_ms"] = 1000
    artifact.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="artifact changed"):
        prepare(paths["learned"], paths["oracle"], tmp_path / "bounded", ReadyWaitSettings(120000))
