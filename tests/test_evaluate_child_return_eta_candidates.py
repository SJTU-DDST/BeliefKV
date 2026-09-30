import json

import numpy as np
import pytest

from scripts.evaluate_child_return_eta_candidates import (
    _paired_workflow_bootstrap, candidates_at_checkpoint, evaluate, predict,
)
from scripts import pilot_child_stream_service_progress as service_module


def _row(project: str, lead: int, rid: str) -> dict:
    return {
        "task": f"{project}__task",
        "project": project,
        "rid": rid,
        "invocation_id": f"child-{rid}",
        "label": "return",
        "return_ts": 6000. + lead,
        "snapshots": [
            {
                "ts_ms": 6000.,
                "content_tail": "I will check this.",
                "content_chars": 64,
                "decode_features": (np.log1p(12), np.log1p(60),
                                    np.log1p(10), 0.),
            },
            {
                "ts_ms": 6300.,
                "content_tail": "In summary, resolved.",
                "content_chars": 180,
                "decode_features": (np.log1p(40), np.log1p(60),
                                    np.log1p(20), 0.),
            },
        ],
    }


def test_checkpoint_features_only_include_prior_snapshots_and_notices():
    row = _row("alpha", 3000, "rid")
    future = {("alpha__task", "child-rid"): [(6301., 2000)]}
    before = candidates_at_checkpoint([row], future, 0)[0]
    assert before["stage"][4] == 0.
    after = candidates_at_checkpoint([row], {
        ("alpha__task", "child-rid"): [(6001., 2000)]
    }, 128)[0]
    assert after["stage"][4] == 1.
    assert after["rolling"][12 + 1] == 1.
    row["snapshots"][1]["content_tail"] = "Future content changed."
    assert candidates_at_checkpoint([row], future, 0)[0] == before
    assert candidates_at_checkpoint([row], future, 128)[0]["rolling"] != after["rolling"]


def test_project_held_out_even_when_extra_training_repeats_task():
    projects = ("alpha", "beta", "gamma", "delta", "epsilon")
    rows = [_row(project, 1200 + i * 250, f"current-{i}")
            for i, project in enumerate(projects)]
    plain, plain_rows = evaluate(rows, {})
    poisoned = _row("alpha", 100_000, "old-alpha")
    extra, extra_rows = evaluate(rows, {}, [([poisoned], {})])
    for threshold in ("0", "128"):
        assert plain["checkpoints"][threshold]["count"] == 5
        assert extra["checkpoints"][threshold]["count"] == 5
        for name in plain["checkpoints"][threshold]["metrics"]:
            assert plain["checkpoints"][threshold]["metrics"][name]["count"] == 5
    for original, augmented in zip(
        (row for row in plain_rows if row["project"] == "alpha"),
        (row for row in extra_rows if row["project"] == "alpha"),
    ):
        assert original["predicted_ms"] == augmented["predicted_ms"]
        assert original["selected_model"] == augmented["selected_model"]


def test_same_request_identity_cannot_be_training_and_evaluation():
    rows = [
        _row(project, 3000, f"rid-{i}")
        for i, project in enumerate(
            ("alpha", "beta", "gamma", "delta", "epsilon")
        )
    ]
    with pytest.raises(ValueError, match="overlap"):
        evaluate(rows, {}, [([rows[0]], {})])


def test_predict_never_returns_negative_eta_and_bootstrap_clusters_tasks():
    train = [
        {
            "project": name, "task": f"{name}__task",
            "actual_remaining_ms": float(lead),
            "base": [1., float(i), 3., 4.],
            "stage": [1., float(i), 3., 4., 0., 0., 0., 0., 0.],
        }
        for i, (name, lead) in enumerate(
            (("alpha", 1000), ("beta", 2000),
             ("gamma", 3000), ("delta", 4000))
        )
    ]
    assert np.all(np.isfinite(predict(train, train, "log_stage")))
    assert np.all(predict(train, train, "log_stage") >= 0.)
    records = [
        {"task": "one", "signed_error_ms": {
            "original_joint": 2000., "nested_choice": 1000.,
        }},
        {"task": "one", "signed_error_ms": {
            "original_joint": 2000., "nested_choice": 1000.,
        }},
        {"task": "two", "signed_error_ms": {
            "original_joint": 500., "nested_choice": 1000.,
        }},
    ]
    outcome = _paired_workflow_bootstrap(records, "nested_choice", draws=200)
    assert outcome["workflow_count"] == 2
    assert outcome["paired_mae_gain_ms"] == 500.


def test_decode_history_only_uses_same_request_service_before_snapshot(
    monkeypatch, tmp_path,
):
    row = _row("alpha", 3000, "rid")
    row["invocation_id"], row["context_id"], row["context_epoch"] = (
        "child-rid", "context-rid", 0,
    )
    row["snapshots"][1]["ts_ms"] = 6700.
    run = tmp_path / "run"
    audit = run / "server/runtime_audit.jsonl"
    audit.parent.mkdir(parents=True)
    workflow = run / "workloads/workflows/alpha__task"
    workflow.mkdir(parents=True)
    (workflow / "child_stream_content.jsonl").touch()

    def event(ts, count, rid="rid"):
        return {
            "event": "gpu_service_sample", "phase": "decode",
            "ts_ms": float(ts), "batch_size": 3,
            "request_samples": [{
                "request_id": rid, "invocation_id": "child-rid",
                "context_id": "context-rid", "context_epoch": 0,
                "token_delta_semantics": "observed_output_ids_delta",
                "output_tokens_before": count - 1, "token_delta": 1,
            }],
        }

    audit.write_text("".join(
        json.dumps(event(ts, count)) + "\n"
        for ts, count in ((6800, 10), (6850, 20), (6890, 30),
                          (7400, 60), (7450, 70), (7590, 80),
                          (7700, 9999))
    ))
    state = {
        "server_end_ms": 9000., "offset_lower_ms": 1000.,
        "offset_upper_ms": 1000.,
        "decode_sample_times_ms": [
            6800., 6850., 6890., 7400., 7450., 7590., 7700.,
        ],
    }
    monkeypatch.setattr(service_module, "collect", lambda *_a, **_k: ([row], {}))
    monkeypatch.setattr(
        service_module, "_service_indices",
        lambda *_a: ({"rid": state}, []),
    )
    selected, coverage = service_module.service_rows(
        [run / "workloads/workflows"], [run],
    )
    assert coverage["supported_rounds"] == 1
    assert len(selected[0]["snapshots"]) == 2
    first, later = selected[0]["snapshots"]
    assert np.expm1(first["decode_history_features"][1]) == pytest.approx(
        (30 - 10) * 1000 / (6890 - 6800),
    )
    assert np.expm1(later["decode_history_features"][1]) == pytest.approx(
        (80 - 10) * 1000 / (7590 - 6800),
    )
