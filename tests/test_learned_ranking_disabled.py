import hashlib
import json

import pytest

from tests import test_workflow_combination_runtime as fixture


def configure_switch(monkeypatch, value):
    original = fixture.configured

    def configured(path):
        config = original(path)
        model = fixture.Path(config["WORKFLOW_WORK_MODEL"])
        raw = json.loads(model.read_bytes())
        raw["remaining_work_ranking_enabled"] = value
        model.write_text(json.dumps(raw))
        config["WORKFLOW_WORK_MODEL_SHA256"] = hashlib.sha256(model.read_bytes()).hexdigest()
        return config

    monkeypatch.setattr(fixture, "configured", configured)


@pytest.mark.parametrize("periodic", (False, True))
def test_disabled_ranking_retains_reference_grants_and_skips_native_summaries(
    monkeypatch, tmp_path, periodic,
):
    configure_switch(monkeypatch, False)
    runtime, admission, _env = fixture.runtime_with_queue(monkeypatch, tmp_path)
    records = []
    runtime._opportunity_writer = fixture.NS(record=records.append)

    def forbidden(*_args):
        raise AssertionError("disabled ranking must not build native combination summaries")

    monkeypatch.setattr(admission._combination, "_resources", forbidden)
    try:
        assert admission.remaining_work_ranking_enabled is False
        if periodic:
            admission.next_tick = 0.
        admission.tick()
        expected = {"b", "c"} if periodic else {"a"}
        assert {item.identity for item in admission.policy.programs.values() if item.active} == expected
        choices = [row for row in records if row["event"] == "workflow_combination_selection"]
        assert choices
        assert all(
            row["decision"]["reason"] == "remaining_work_ranking_disabled_reference"
            and row["decision"]["selected"] == row["decision"]["reference"]
            for row in choices
        )
        assert admission._work is not None
        assert admission._deferred_dispatch is admission._combination
    finally:
        runtime._opportunity_writer = None
        runtime.close()


@pytest.mark.parametrize("value", (0, 1, "false", None))
def test_ranking_switch_rejects_non_boolean_values(monkeypatch, tmp_path, value):
    configure_switch(monkeypatch, value)
    with pytest.raises(ValueError, match="ranking switch must be a boolean"):
        fixture.runtime_with_queue(monkeypatch, tmp_path)
