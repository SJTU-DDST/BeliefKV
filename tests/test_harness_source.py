import json

import pytest

from beliefkv.experiments.harness_source import (
    NATIVE_EVENTS, NATIVE_STATUS, NATIVE_TOKENS, register_harness_source,
)
from beliefkv.runtime.replay_token_capture import capture_record
from scripts.run_causal_gpu_replay import source_plan
from tests.test_causal_replay import async_spawn_trace


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as output:
        for row in rows:
            output.write(json.dumps(row) + "\n")


@pytest.fixture
def capture(tmp_path):
    trace = async_spawn_trace()
    write_jsonl(tmp_path / "client/events.jsonl", (event.to_dict() for event in trace.ordered()))
    write_jsonl(tmp_path / NATIVE_EVENTS, trace.native)
    tokens = []
    for event in trace.native:
        request = {
            **event, "ts_ms": 1., "request_id": event["attributes"]["request_id"],
            "_replay_request_parameters": {
                "max_new_tokens": 10, "min_new_tokens": 0, "ignore_eos": False,
                "stop": [], "stop_regex": [], "stop_token_ids": None,
            },
            "_replay_finish_reason": {"type": "stop", "matched": 9},
        }
        if event["kind"] == "llm_submit":
            tokens.append(capture_record(request, [3] * 100_000, event="request_prompt"))
        else:
            tokens.append(capture_record(request, [3, 4, 5, 9], event="request_output"))
    write_jsonl(tmp_path / NATIVE_TOKENS, tokens)
    (tmp_path / NATIVE_STATUS).write_text(json.dumps({
        "replay_token_capture": True, "writer_error": None, "failed_records": 0,
        "dropped_records": 0, "writer_queue_depth": 0, "pending_batch_count": 0,
    }))
    return tmp_path


def register(capture, **overrides):
    return register_harness_source(capture, **{
        "harness": "codex", "harness_revision": "test-revision",
        "dataset": "test-dataset", "dataset_revision": "test-commit",
        "client_event_files": ["client/events.jsonl"], "workflow_task_ids": {"w": "first"},
        **overrides,
    })


def test_observed_harness_and_exact_native_tokens_use_the_normal_source_entry(capture):
    manifest = register(capture)
    plan, expected, evidence = source_plan(capture)
    assert len(plan.request_usage) == len(expected) == 5
    assert evidence["source_kind"] == "observed_external_harness"
    assert evidence["workflow_task_ids"] == {"w": "first"}
    assert evidence["token_source_sha256"] == manifest["artifacts_sha256"][NATIVE_TOKENS]
    assert evidence["lifecycle"]["parent_model_calls_observed_after_spawn_before_wait"] == 1
    assert evidence["lifecycle"]["observed_join_modes"] == {"any": 1}
    with pytest.raises(FileExistsError):
        register(capture)
    with (capture / "client/events.jsonl").open("a") as output:
        output.write("\n")
    with pytest.raises(ValueError, match="changed after registration"):
        source_plan(capture)


@pytest.mark.parametrize("failure,match", (
    ("task", "task binding"),
    ("drain", "drained"),
    ("partial", "incomplete capture"),
    ("tokens", "incomplete JSONL"),
))
def test_unverified_harness_capture_does_not_enter_replay(capture, failure, match):
    fields = {}
    if failure == "task":
        fields["workflow_task_ids"] = {"missing": "loco-id"}
    elif failure == "drain":
        status = capture / NATIVE_STATUS
        status.write_text(json.dumps({**json.loads(status.read_bytes()), "pending_batch_count": 1}))
    else:
        file = NATIVE_TOKENS if failure == "tokens" else "client/events.jsonl"
        with (capture / file).open("a") as output:
            output.write("{")
    with pytest.raises(ValueError, match=match):
        register(capture, **fields)
