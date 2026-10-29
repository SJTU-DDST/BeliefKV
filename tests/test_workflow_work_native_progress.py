from copy import deepcopy
import json
from types import SimpleNamespace as NS

import pytest

from beliefkv.core.events import RelationType, RuntimeEvent, RuntimeEventKind as Kind
from beliefkv.predictor.workflow_work import FEATURES, WorkflowWorkModel
from beliefkv.runtime.replay_token_capture import encode_tokens
from beliefkv.runtime.workflow_work_worker import WorkflowWorkWorker
from experiments.reports.workflow_work_training_20261029.qualify_input_contract import (
    qualify_progress_observations,
)
from experiments.reports.workflow_work_training_20261029.refresh_native_progress import (
    replace_observations,
)
from scripts.train_workflow_work import (
    BOUNDARY_PROGRESS, NATIVE_PROGRESS, _native_progress_records, export_capture,
    native_decode_progress, progress_contract,
)
from tests.test_workflow_work import model_raw


def event(kind, when, request=None, **kwargs):
    return RuntimeEvent(
        f"{kind.value}-{when}", when, kind, "wf", invocation_id="root",
        context_id="context", context_epoch=0,
        attributes={} if request is None else {"request_id": request},
        **kwargs,
    )


def boundaries():
    return [
        event(Kind.LLM_SUBMIT, 1., "r1"), event(Kind.LLM_RESULT, 5., "r1"),
        event(Kind.LLM_SUBMIT, 6., "r2"), event(Kind.LLM_RESULT, 8., "r2"),
    ]


def sample(when, count, *, request="r1", phase="decode", identity=None):
    return {
        "event": "gpu_service_sample", "sample_id": identity or f"batch-{when}",
        "complete_monotonic_ms": when, "monotonic_clock_domain": "same-host",
        "phase": phase, "request_samples": [{
            "request_id": request, "workflow_id": "wf", "invocation_id": "root",
            "context_id": "context", "context_epoch": 0,
            "output_tokens_before": max(0, count - 1), "token_delta": min(1, count),
            "token_delta_semantics": "observed_output_ids_delta",
        }],
    }


def batches():
    return [
        sample(1.1, 1, phase="prefill"), sample(2., 128),
        sample(3., 256), sample(4., 300),
        sample(7., 1, request="r2", phase="prefill"),
    ]


def test_native_progress_uses_partial_counts_and_excludes_final_output():
    rows = native_decode_progress(boundaries(), batches(), {"r1": 300, "r2": 1})
    assert [(row["ts_ms"], row["output_tokens"]) for row in rows] == [(2., 128), (3., 256)]


def test_native_progress_suppresses_duplicate_or_regressed_counts_below_quantum():
    records = [
        sample(2., 128), sample(2.1, 128), sample(2.2, 64),
        sample(2.3, 255), sample(3., 256), sample(7., 1, request="r2"),
    ]
    rows = native_decode_progress(boundaries(), records, {"r1": 300, "r2": 1})
    assert [row["output_tokens"] for row in rows] == [128, 256]


def test_native_progress_excludes_same_timestamp_as_client_completion():
    records = [sample(5., 128), sample(7., 1, request="r2")]
    assert native_decode_progress(boundaries(), records, {"r1": 300, "r2": 1}) == []


def test_completed_zero_delta_residue_does_not_publish_progress():
    records = batches()
    trailing = sample(5.1, 299)
    trailing["request_samples"][0].update(output_tokens_before=299, token_delta=0)
    records.insert(-1, trailing)
    assert [row["output_tokens"] for row in native_decode_progress(
        boundaries(), records, {"r1": 300, "r2": 1},
    )] == [128, 256]
    trailing["request_samples"][0]["token_delta"] = 1
    with pytest.raises(ValueError, match="follows source completion"):
        native_decode_progress(boundaries(), records, {"r1": 300, "r2": 1})


@pytest.mark.parametrize("field,value", (
    ("workflow_id", "other"), ("invocation_id", "other"),
    ("context_id", "other"), ("context_epoch", 1),
    ("token_delta_semantics", "estimated"),
    ("output_tokens_before", -1), ("token_delta", True),
))
def test_native_progress_rejects_wrong_owner_or_unobserved_counts(field, value):
    records = batches()
    records[1]["request_samples"][0][field] = value
    with pytest.raises(ValueError):
        native_decode_progress(boundaries(), records, {"r1": 300, "r2": 1})


@pytest.mark.parametrize("change", (
    "duplicate_sample", "clock_domain", "nonfinite_time", "reversed_time",
    "before_submit", "after_result", "missing_coverage", "missing_result", "overrun",
))
def test_native_progress_rejects_invalid_clock_coverage_or_completion(change):
    events, records = boundaries(), batches()
    if change == "duplicate_sample":
        records[2]["sample_id"] = records[1]["sample_id"]
    elif change == "clock_domain":
        records[2]["monotonic_clock_domain"] = "other-host"
    elif change == "nonfinite_time":
        records[2]["complete_monotonic_ms"] = float("nan")
    elif change == "reversed_time":
        records[2]["complete_monotonic_ms"] = 1.5
    elif change == "before_submit":
        records[0]["complete_monotonic_ms"] = .5
    elif change == "after_result":
        records[-1]["complete_monotonic_ms"] = 9.
    elif change == "missing_coverage":
        records.pop()
    elif change == "missing_result":
        events.pop()
    elif change == "overrun":
        records[2]["request_samples"][0]["output_tokens_before"] = 300
    with pytest.raises(ValueError):
        native_decode_progress(events, records, {"r1": 300, "r2": 1})


def test_native_progress_ignores_future_hints_and_unqualified_workflows():
    records = batches()
    expected = native_decode_progress(boundaries(), records, {"r1": 300, "r2": 1})
    for record in records:
        record["future_work"] = 10 ** 9
        record["request_samples"][0]["remaining_output"] = 10 ** 9
        record["request_samples"].append({"request_id": "rejected-workflow-call"})
    assert native_decode_progress(boundaries(), records, {"r1": 300, "r2": 1}) == expected


def make_capture(tmp_path):
    capture = tmp_path / "capture"
    workflow = capture / "client_0/workflows/wf"
    workflow.mkdir(parents=True)
    (workflow / "trajectory.json").write_text(json.dumps([{
        "type": "human", "content": "Project: test\nTask requirements:\nab implement.",
    }]))
    (workflow / "result.json").write_text(json.dumps({"natural_terminal": True}))
    events = [
        event(Kind.WORKFLOW_START, 0.),
        event(Kind.INVOCATION_CREATE, .1, relation_type=RelationType.ROOT),
        *boundaries(), event(Kind.WORKFLOW_END, 9.),
    ]
    (workflow / "runtime_events.deepagents.jsonl").write_text(
        "".join(json.dumps(row.to_dict()) + "\n" for row in events),
    )
    server = capture / "server"
    server.mkdir()
    first_prompt, first_output = (1, 2, 3), (4,) * 300
    records = []
    for request, prompt, output in (
        ("r1", first_prompt, first_output),
        ("r2", first_prompt + first_output + (5,), (6,)),
    ):
        for kind, tokens in (("request_prompt", prompt), ("request_output", output)):
            records.append({"event": kind, "request_id": request, **encode_tokens(tokens)})
    (server / "request_tokens.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records),
    )
    (server / "runtime_audit.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in batches()),
    )
    return capture


def test_export_streaming_has_running_features_and_charges_output_once(tmp_path):
    capture = make_capture(tmp_path)
    original, _, rejected = export_capture(capture, prefill_weight=.01)
    streamed, evidence, streamed_rejected = export_capture(
        capture, prefill_weight=.01, native_progress=True,
    )
    assert rejected == streamed_rejected == []
    assert len(streamed) == len(original) + 2
    observations = [row for row in streamed if row["event"] == "native_decode_progress"]
    assert [row["observed_output_tokens"] for row in observations] == [128, 256]
    for row in observations:
        features = dict(zip(FEATURES, row["features"]))
        assert features["running"] == 1 and features["ready"] == 0
        assert features["output_tokens"] == row["observed_output_tokens"]
        assert features["input_tokens"] == 0
    assert {row["total_work"] for row in original + streamed} == {301.04}
    assert streamed[-1]["features"][FEATURES.index("output_tokens")] == 301
    assert streamed[-1]["features"][FEATURES.index("input_tokens")] == 4
    assert evidence[str((capture / "server/runtime_audit.jsonl").resolve())]
    assert not any(row["features"][FEATURES.index("running")] for row in original)
    assert [
        {key: value for key, value in row.items() if key != "snapshot"}
        for row in streamed if row["event"] != "native_decode_progress"
    ] == [
        {key: value for key, value in row.items() if key != "snapshot"} for row in original
    ]


def test_default_export_does_not_require_native_progress_journal(tmp_path):
    capture = make_capture(tmp_path)
    (capture / "server/runtime_audit.jsonl").unlink()
    assert export_capture(capture, prefill_weight=.01)[0]
    with pytest.raises(FileNotFoundError):
        export_capture(capture, prefill_weight=.01, native_progress=True)


def test_real_worker_matches_partial_decode_features_and_probabilities(tmp_path):
    capture = make_capture(tmp_path)
    offline, _, _ = export_capture(capture, prefill_weight=.01, native_progress=True)
    path = next(capture.glob("client_*/workflows/*/runtime_events.deepagents.jsonl"))
    events = [RuntimeEvent.from_dict(json.loads(line)) for line in path.open()]
    observations = native_decode_progress(events, batches(), {"r1": 300, "r2": 1})
    prompt, output = (1, 2, 3), (4,) * 300
    values = {
        "r1": NS(prompt_tokens=prompt, output_tokens=output),
        "r2": NS(prompt_tokens=prompt + output + (5,), output_tokens=(6,)),
    }
    model = WorkflowWorkModel(model_raw())
    worker = WorkflowWorkWorker("", "", model=model, autostart=False)
    tokenizer = NS(decode=lambda *_args, **_kwargs: (
        "<|im_start|>user\nTask requirements:\nab implement.<|im_end|>"
    ))
    try:
        checks = qualify_progress_observations(
            worker, events, observations, NS(get=values.__getitem__), offline, model,
            tokenizer=tokenizer,
        )
        assert checks["events"]["native_decode_progress"] == 2
        assert sum(checks["events"].values()) == len(offline)
        assert checks["prediction_checks"] == len(offline) - 1
    finally:
        worker.close()


@pytest.mark.parametrize("change", (None, "population", "total", "execution"))
def test_progress_repair_keeps_fixed_executions_and_total_work(tmp_path, change):
    capture = make_capture(tmp_path)
    old, _, _ = export_capture(capture, prefill_weight=.01)
    rows, evidence, rejected = export_capture(capture, prefill_weight=.01, native_progress=True)
    original = {"rows": old, "prefill_token_weight": .01, "work_unit": "logical_proxy"}
    if change == "population":
        rows.clear()
    elif change is not None:
        key = "total_work" if change == "total" else "source_workflow_sha256"
        rows = deepcopy(rows)
        for row in rows:
            row[key] = 0
    if change is not None:
        with pytest.raises(ValueError, match="progress repair changed"):
            replace_observations(original, rows, evidence, rejected)
    else:
        repaired = replace_observations(original, rows, evidence, rejected)
        assert repaired["rows"] == rows
        assert repaired["progress_observation_contract"] == NATIVE_PROGRESS


def test_native_progress_journal_rejects_truncated_tail(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps(sample(2., 128)))
    with pytest.raises(ValueError, match="complete service journal"):
        list(_native_progress_records(path))


def test_progress_observation_contract_is_explicit_and_legacy_default_is_stable():
    assert progress_contract({}) == BOUNDARY_PROGRESS
    assert progress_contract({"progress_observation_contract": NATIVE_PROGRESS}) == NATIVE_PROGRESS
    with pytest.raises(ValueError, match="unsupported"):
        progress_contract({"progress_observation_contract": "future_lengths"})
