from pathlib import Path

import yaml


def test_default_minisweagent_root_has_long_workflow_deadline() -> None:
    config_path = (
        Path(__file__).resolve().parents[1]
        / "configs/workloads/minisweagent_qwen2_5_7b_reactive.yaml"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["agent"]["wall_time_limit_seconds"] == 7200
    assert config["environment"]["timeout"] == 120
